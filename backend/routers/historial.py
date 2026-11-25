"""
Endpoint de historial de acciones — requisito R-16 (Historial de
acciones). Es de SOLO LECTURA: la tabla `acciones` se llena sola desde:

- servicios/control_energetico.py (R-13): cada apagado/reactivación del
  algoritmo de supervivencia, con actor_tipo='sistema' y actor_ref='R-13'.
- routers/dispositivos.py, PATCH .../dispositivos/{id} (R-10/R-11): cada
  cambio manual de `estado`, con el rol del usuario en el domo y su correo.

GET /domos/{domo_id}/historial
    Lista las acciones de ese domo, de más reciente a más antigua, con
    límite (por defecto 100) y filtros opcionales: fecha_desde/fecha_hasta,
    dispositivo_id y rol ('sistema', 'dueño' o 'administrador').

Modelo v2: `acciones` cuelga solo de `dispositivos`; el domo sale por JOIN. La
respuesta conserva la forma de v1: `rol` es 'sistema', 'dueño' o
'administrador'; `accion_realizada`, `valor_anterior`, `valor_nuevo` y
`condicion_control` son `accion`, `estado_anterior`, `estado_nuevo` y
`condicion`; `usuario_id` y `nombre_usuario` salen de la cuenta cuyo correo es
`actor_ref` (si todavía existe con ese correo; sin FK, el historial no depende
de la cuenta).

Acceso: dueño o administrador del domo (routers/domos.py). Sin acceso, 404.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

from database import get_db
from models import ACTOR_SISTEMA, ROL_API, Accion, Dispositivo, Usuario
from routers.domos import domo_accesible
from schemas import HistorialAccionOut
from security import get_current_user

router = APIRouter(prefix="/domos/{domo_id}/historial", tags=["Historial de acciones"])

# rol de la API (como en v1) -> actor_tipo de la tabla acciones.
ACTOR_DE_ROL = {"sistema": ACTOR_SISTEMA, **{api: tabla for tabla, api in ROL_API.items()}}


def rol_de_actor(actor_tipo: str) -> str:
    return ROL_API.get(actor_tipo, actor_tipo)


def _salida(accion: Accion, dispositivo: Dispositivo, usuario: Optional[Usuario]) -> HistorialAccionOut:
    return HistorialAccionOut(
        id=accion.id,
        domo_id=dispositivo.domo_id,
        dispositivo_id=accion.dispositivo_id,
        nombre_dispositivo=dispositivo.nombre,
        usuario_id=usuario.id if usuario is not None else None,
        nombre_usuario=usuario.nombre if usuario is not None else None,
        rol=rol_de_actor(accion.actor_tipo),
        accion_realizada=accion.accion,
        valor_anterior=accion.estado_anterior,
        valor_nuevo=accion.estado_nuevo,
        condicion_control=accion.condicion,
        resultado=accion.resultado,
        fecha_hora=accion.fecha_hora,
    )


@router.get("", response_model=list[HistorialAccionOut])
def listar_historial(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
    limite: int = Query(100, ge=1, le=1000),
    fecha_desde: Optional[datetime] = None,
    fecha_hasta: Optional[datetime] = None,
    dispositivo_id: Optional[int] = None,
    rol: Optional[str] = None,
):
    domo = domo_accesible(db, domo_id, usuario_actual)

    consulta = (
        db.query(Accion, Dispositivo, Usuario)
        .join(Dispositivo, Dispositivo.id == Accion.dispositivo_id)
        # LEFT: una acción del sistema no tiene cuenta, y una cuenta pudo cambiar de correo.
        .outerjoin(Usuario, and_(Usuario.correo == Accion.actor_ref, Accion.actor_tipo != ACTOR_SISTEMA))
        .filter(Dispositivo.domo_id == domo.id)
    )
    if fecha_desde is not None:
        consulta = consulta.filter(Accion.fecha_hora >= fecha_desde)
    if fecha_hasta is not None:
        consulta = consulta.filter(Accion.fecha_hora <= fecha_hasta)
    if dispositivo_id is not None:
        consulta = consulta.filter(Accion.dispositivo_id == dispositivo_id)
    if rol is not None:
        consulta = consulta.filter(Accion.actor_tipo == ACTOR_DE_ROL.get(rol, rol))

    # Desempate por id: dos acciones pueden compartir el mismo fecha_hora.
    filas = consulta.order_by(Accion.fecha_hora.desc(), Accion.id.desc()).limit(limite).all()
    return [_salida(accion, dispositivo, usuario) for accion, dispositivo, usuario in filas]
