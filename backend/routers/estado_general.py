"""
Endpoint de estado general del domo — requisito R-5 (Visualización del
estado general del domo). Es de SOLO LECTURA: agrega en una sola
respuesta datos que ya existen en otras tablas (domos, lecturas_*,
dispositivos, alertas) — no crea ni modifica nada.

GET /domos/{domo_id}/estado-general
    Devuelve: datos básicos del domo, la última lectura de cada tipo
    (fotovoltaica, consumo, batería — cada una None si ese domo no tiene
    ninguna todavía), cantidad de dispositivos conectados sobre el total,
    cantidad de alertas activas, y la fecha de actualización más reciente
    entre las tres lecturas que sí existan.

Sobre `dispositivos_conectados` — catálogo de `estado_conexion`:
docs/Requisitos/Variables.md define un catálogo explícito para el campo
`estado` de dispositivos (sección 6: encendido/apagado/desconectado/
error), pero NO define ningún catálogo de valores para `estado_conexion`
— ese campo aparece solo conceptualmente en la sección 10 ("Variables de
comunicación") como `conectado: Boolean`. La columna real en el modelo
(models.py) es Text, no booleana, y ningún otro documento del proyecto
fija otro valor. Por eso "conectado" se cuenta como
`Dispositivo.conectado` (v2: boolean; antes estado_conexion == 'conectado'): es el único valor de texto
consistente con esa semántica booleana documentada — no un valor
inventado sin respaldo.

Sobre por qué este router escribe sus propias consultas de "última
lectura" en vez de reutilizar routers/lecturas_fotovoltaicas.py,
lecturas_consumo.py, lecturas_bateria.py o alertas.py: en esos routers,
la lógica de "última lectura" vive DENTRO de la función-endpoint de
FastAPI (con `Depends()` ya resueltos por el framework al recibir una
petición HTTP) — no son funciones puras separadas que se puedan importar
y llamar directamente. Ya existe un precedente en el proyecto para este
mismo caso: routers/control_energetico.py necesita "la última lectura de
batería" y, en vez de importar nada de lecturas_bateria.py, define su
propio helper local `_ultima_lectura_bateria`. Este router sigue el mismo
criterio: consultas de 4-5 líneas, específicas de una tabla, se escriben
frescas en cada router que las necesita — lo único verdaderamente
transversal que SÍ se reutiliza es el control de acceso
(_domos_accesibles/DOMO_NO_ENCONTRADO de routers/domos.py).

Medidas actuales (DB-3): potencia generada, potencia consumida, SoC y estado de la
batería se devuelven solo si su último valor tiene como mucho mediciones.VIGENCIA_S
(120 s, la misma ventana que la tarjeta de Autonomía del Panel); si es más viejo van en
null ("sin dato"), sin inventar nada. Cada medida se mira por separado: un ESP32 sin
sensor que publica null no deja ver como actual un valor de días atrás.
`fecha_actualizacion` sigue siendo la del último dato de cualquiera de los tres temas,
aunque sea viejo: es lo que dice "Actualizado hace …".

Acceso: dueño propietario del domo, o administrador con una asignación
activa en administrador_domo — mismo criterio que el resto del proyecto.
"""


from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Alerta, Dispositivo, Domo, Usuario
from routers.domos import DOMO_NO_ENCONTRADO, _domos_accesibles
from schemas import EstadoGeneralDomoOut
from security import get_current_user
from servicios import mediciones

router = APIRouter(prefix="/domos/{domo_id}/estado-general", tags=["Estado general del domo"])


def _domo_accesible(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo asegurando que el usuario autenticado tenga acceso a él (dueño
    propietario o administrador con asignación activa) — reutiliza
    _domos_accesibles() de routers/domos.py en vez de repetir el criterio."""
    domo = _domos_accesibles(db, usuario).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    return domo


@router.get("", response_model=EstadoGeneralDomoOut)
def obtener_estado_general(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)

    # La medición más reciente con datos de cada tema (modelo v2: una sola tabla).
    ultima_fv = mediciones.ultima(db, domo.id, "fotovoltaica")
    ultima_consumo = mediciones.ultima(db, domo.id, "consumo")
    ultima_bateria = mediciones.ultima(db, domo.id, "bateria")

    # Solo valores actuales (ver el docstring del módulo).
    desde = mediciones.vigente_desde()

    def actual(columna):
        dato = mediciones.ultimo_valor(db, domo.id, columna, desde)
        return dato[0] if dato is not None else None

    # Los desactivados no cuentan, igual que en el listado de dispositivos.
    dispositivos_activos = db.query(Dispositivo).filter(Dispositivo.domo_id == domo.id, Dispositivo.activo.is_(True))
    dispositivos_total = dispositivos_activos.count()
    dispositivos_conectados = dispositivos_activos.filter(Dispositivo.conectado.is_(True)).count()

    alertas_activas = (
        db.query(Alerta)
        .filter(Alerta.domo_id == domo.id, Alerta.estado == "activa")
        .count()
    )

    fechas = [
        lectura.fecha_hora
        for lectura in (ultima_fv, ultima_consumo, ultima_bateria)
        if lectura is not None
    ]
    fecha_actualizacion = max(fechas) if fechas else None

    return EstadoGeneralDomoOut(
        domo_id=domo.id,
        nombre_domo=domo.nombre,
        estado_domo=domo.estado_domo,
        estado_conexion=domo.estado_conexion,
        potencia_generada=actual("potencia_fv_w"),
        potencia_consumida=actual("potencia_consumo_w"),
        estado_bateria=actual("estado_bateria"),
        estado_carga_soc=actual("soc_pct"),
        dispositivos_conectados=dispositivos_conectados,
        dispositivos_total=dispositivos_total,
        alertas_activas=alertas_activas,
        fecha_actualizacion=fecha_actualizacion,
    )
