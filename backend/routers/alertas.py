"""
Endpoints de alertas — requisito R-15 (Sistema de alertas).

POST /domos/{domo_id}/alertas
    Crea una alerta. `estado_alerta` queda 'activa' por defecto (default
    del modelo). `fecha_hora` se asigna automáticamente si no se envía.
    v2: `tipo_alerta`, `nivel_prioridad` y `descripcion` son obligatorios, y
    `id_fisico_ref` (opcional, texto sin FK) dice de qué dispositivo es. Una alerta
    creada aquí nunca es `creada_por_r13`: R-13 no la cierra.

    TODO (trabajo futuro, fuera del alcance de R-15): esto por ahora sirve
    para simular alertas sin generación automática real. Más adelante
    debería existir lógica que revise las lecturas entrantes (R-6/R-7/
    R-8/R-9) o el algoritmo de supervivencia (R-13) y cree alertas sola
    cuando algo cruce un límite — ese trabajo no se implementa aquí.

GET /domos/{domo_id}/alertas
    Lista alertas de ese domo, de más reciente a más antigua, con límite
    (por defecto 100) y filtros opcionales por rango de fechas,
    `estado_alerta` (p. ej. solo 'activa'), `nivel_prioridad` e `id_fisico_ref`
    (las alertas de un dispositivo).

PATCH /domos/{domo_id}/alertas/{alerta_id}
    Cambia `estado_alerta` (p. ej. marcarla como 'atendida' o 'cerrada').
    Es lo único que este endpoint permite modificar.

Acceso: dueño o administrador del domo (asignaciones) — mismo criterio de _domos_accesibles() de
routers/domos.py (reutilizado, no reimplementado) para los tres
endpoints; no hay aquí ninguna operación exclusiva del dueño. 404 en vez
de 403 si el usuario no tiene acceso al domo o la alerta no es de ese
domo, para no revelar su existencia (mismo patrón en todo el proyecto —
ver nota sobre RLS en routers/domos.py y docs/arquitectura_sistema.md).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import Alerta, Domo, Usuario
from routers.domos import DOMO_NO_ENCONTRADO, _domos_accesibles
from schemas import AlertaCreate, AlertaOut, AlertaUpdate
from security import get_current_user

router = APIRouter(prefix="/domos/{domo_id}/alertas", tags=["Alertas"])

ALERTA_NO_ENCONTRADA = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Alerta no encontrada.",
)


def _domo_accesible(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo asegurando que el usuario autenticado tenga acceso a él (dueño
    propietario o administrador con asignación activa) — reutiliza
    _domos_accesibles() de routers/domos.py en vez de repetir el criterio."""
    domo = _domos_accesibles(db, usuario).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    return domo


def _alerta_del_domo(db: Session, domo_id: int, alerta_id: int) -> Alerta:
    """Alerta acotada siempre por domo_id, para no exponer una de otro
    domo a quien adivine su id (mismo patrón que
    routers/dispositivos.py::_dispositivo_del_domo)."""
    alerta = (
        db.query(Alerta)
        .filter(Alerta.id == alerta_id, Alerta.domo_id == domo_id)
        .first()
    )
    if alerta is None:
        raise ALERTA_NO_ENCONTRADA
    return alerta


@router.post("", response_model=AlertaOut, status_code=status.HTTP_201_CREATED)
def crear_alerta(
    domo_id: int,
    datos: AlertaCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)

    # fecha_hora solo se fija si el cliente la envió explícitamente; si no,
    # se omite el atributo para que la base de datos aplique su propio
    # server_default=func.now() (ver models.py) al insertar.
    campos_fecha = {}
    if datos.fecha_hora is not None:
        campos_fecha["fecha_hora"] = datos.fecha_hora

    nueva_alerta = Alerta(
        domo_id=domo.id,
        tipo_alerta=datos.tipo_alerta,
        descripcion=datos.descripcion,
        nivel_prioridad=datos.nivel_prioridad,
        variable_asociada=datos.variable_asociada,
        id_fisico_ref=datos.id_fisico_ref,
        valor_detectado=datos.valor_detectado,
        valor_limite=datos.valor_limite,
        # estado_alerta usa su default de modelo ("activa").
        **campos_fecha,
    )
    db.add(nueva_alerta)
    db.commit()
    db.refresh(nueva_alerta)
    return nueva_alerta


@router.get("", response_model=list[AlertaOut])
def listar_alertas(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
    limite: int = Query(100, ge=1, le=1000),
    fecha_desde: Optional[datetime] = None,
    fecha_hasta: Optional[datetime] = None,
    estado_alerta: Optional[str] = None,
    nivel_prioridad: Optional[str] = None,
    id_fisico_ref: Optional[str] = None,
):
    domo = _domo_accesible(db, domo_id, usuario_actual)

    consulta = db.query(Alerta).filter(Alerta.domo_id == domo.id)
    if fecha_desde is not None:
        consulta = consulta.filter(Alerta.fecha_hora >= fecha_desde)
    if fecha_hasta is not None:
        consulta = consulta.filter(Alerta.fecha_hora <= fecha_hasta)
    if estado_alerta is not None:
        consulta = consulta.filter(Alerta.estado_alerta == estado_alerta)
    if nivel_prioridad is not None:
        consulta = consulta.filter(Alerta.nivel_prioridad == nivel_prioridad)
    if id_fisico_ref is not None:
        consulta = consulta.filter(Alerta.id_fisico_ref == id_fisico_ref)

    # Desempate por id: dos alertas pueden compartir el mismo fecha_hora
    # (columnas de segundo de resolución, o varias inserciones simuladas
    # muy seguidas), y sin un segundo criterio el orden entre ellas
    # quedaría indefinido — mismo patrón que en los routers de lecturas.
    return (
        consulta.order_by(Alerta.fecha_hora.desc(), Alerta.id.desc())
        .limit(limite)
        .all()
    )


@router.patch("/{alerta_id}", response_model=AlertaOut)
def actualizar_alerta(
    domo_id: int,
    alerta_id: int,
    datos: AlertaUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)
    alerta = _alerta_del_domo(db, domo.id, alerta_id)

    alerta.cambiar_estado(datos.estado_alerta)

    db.commit()
    db.refresh(alerta)
    return alerta
