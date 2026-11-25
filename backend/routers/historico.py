"""
Endpoint de históricos — requisito R-14 (Visualización de históricos). Es
de SOLO LECTURA y de AGREGADOS: no crea ninguna tabla ni dato nuevo, cruza
lecturas_fotovoltaicas y lecturas_consumo de un rango de fechas.

GET /domos/{domo_id}/historico?fecha_desde=...&fecha_hasta=...
    Ambos parámetros son OBLIGATORIOS — el "periodo de consulta" es parte
    explícita de lo que pide R-14 (ver catálogo de variables en el PDF de
    requisitos), a diferencia de fecha_desde/fecha_hasta opcionales en
    los routers de lecturas (R-6/R-7/R-8/R-9), donde son solo un filtro
    adicional sobre un listado que también tiene sentido sin filtrar.

    Devuelve, para ese rango:
    - energia_generada_total: SUM(lecturas_fotovoltaicas.energia_generada)
    - energia_consumida_total: SUM(lecturas_consumo.energia_consumida)
    - potencia_generada_maxima / _promedio: MAX/AVG(...potencia_generada)
    - potencia_consumida_maxima / _promedio: MAX/AVG(...potencia_consumida)

Catálogo de variables — qué NO incluye y por qué: el PDF de requisitos
(REQUISITOS DE LA APLICACIÓN.pdf, fila R-14) lista exactamente: "ID del
domo, Variable, Valor, Unidad, Fecha, Hora, periodo de consulta, energía
generada, energía consumida, potencia máxima, potencia promedio". No
incluye batería, SoC, ni ninguna otra variable — así que este endpoint
tampoco agrega nada de lecturas_bateria (no es un olvido, es no inventar
un requisito que el documento no pide — ver docs/Requisitos/).

Validación de fecha_desde/fecha_hasta:
- fecha_desde > fecha_hasta → 422. Es un rango invertido, señal de un
  bug en quien llama (parámetros intercambiados) — dejarlo pasar y
  devolver todo en null ocultaría ese error en vez de avisarlo.
- fecha_desde == fecha_hasta → válido (rango de un solo instante).
- Rango válido pero sin ninguna lectura de ningún tipo → 200 con todos
  los campos numéricos en null (no 404, no ceros): la petición en sí es
  correcta, simplemente no hay datos para ese período — ver el docstring
  de HistoricoDomoOut en schemas.py sobre por qué null y no 0.

Resolución de las consultas de agregación: SUM/MAX/AVG de SQLAlchemy
(func.sum/func.max/func.avg) sobre cada tabla por separado, filtrando por
domo_id y el rango de fechas — dos queries independientes (una por
lecturas_fotovoltaicas, otra por lecturas_consumo), no un único JOIN
entre ambas tablas: cruzarlas por fecha_hora exacta perdería filas (los
timestamps de generación y consumo casi nunca coinciden exacto) y un
JOIN por rango produciría un producto cartesiano que rompe los SUM/AVG.
Una consulta de agregación sin GROUP BY siempre devuelve exactamente una
fila (con NULLs si no hay datos que coincidan), así que no hace falta
manejar "sin resultados" como caso especial de la consulta en sí.

Acceso: dueño propietario del domo, o administrador con una asignación
activa en administrador_domo — mismo criterio que el resto del proyecto.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Domo, Medicion, Usuario
from routers.domos import DOMO_NO_ENCONTRADO, _domos_accesibles
from schemas import HistoricoDomoOut
from security import get_current_user

router = APIRouter(prefix="/domos/{domo_id}/historico", tags=["Históricos"])


def _domo_accesible(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo asegurando que el usuario autenticado tenga acceso a él (dueño
    propietario o administrador con asignación activa) — reutiliza
    _domos_accesibles() de routers/domos.py en vez de repetir el criterio."""
    domo = _domos_accesibles(db, usuario).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    return domo


@router.get("", response_model=HistoricoDomoOut)
def obtener_historico(
    domo_id: int,
    fecha_desde: datetime = Query(..., description="Inicio del período de consulta (inclusive)."),
    fecha_hasta: datetime = Query(..., description="Fin del período de consulta (inclusive)."),
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)

    if fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=422,
            detail="fecha_desde no puede ser posterior a fecha_hasta.",
        )

    # Modelo v2: una sola tabla. SUM/MAX/AVG ignoran los NULL, así que cada
    # agregado solo cuenta los bloques con dato de esa medida.
    (
        energia_generada_total,
        potencia_generada_maxima,
        potencia_generada_promedio,
        energia_consumida_total,
        potencia_consumida_maxima,
        potencia_consumida_promedio,
    ) = (
        db.query(
            func.sum(Medicion.energia_fv_wh),
            func.max(Medicion.potencia_fv_w),
            func.avg(Medicion.potencia_fv_w),
            func.sum(Medicion.energia_consumo_wh),
            func.max(Medicion.potencia_consumo_w),
            func.avg(Medicion.potencia_consumo_w),
        )
        .filter(
            Medicion.domo_id == domo.id,
            Medicion.fecha_hora >= fecha_desde,
            Medicion.fecha_hora <= fecha_hasta,
        )
        .one()
    )

    return HistoricoDomoOut(
        domo_id=domo.id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        energia_generada_total=energia_generada_total,
        energia_consumida_total=energia_consumida_total,
        potencia_generada_maxima=potencia_generada_maxima,
        potencia_generada_promedio=potencia_generada_promedio,
        potencia_consumida_maxima=potencia_consumida_maxima,
        potencia_consumida_promedio=potencia_consumida_promedio,
    )
