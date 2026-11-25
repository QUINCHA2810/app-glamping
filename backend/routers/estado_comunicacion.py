"""
Endpoints de estado de comunicación — requisito R-17 (Estado de
comunicación).

Modelo v2: ya no hay tabla estado_comunicacion ni historial. El estado es el
ÚLTIMO, en dos columnas del dispositivo: `conectado` (boolean) y
`ultima_comunicacion`. Las rutas y la forma de la respuesta se conservan:

POST .../estado-comunicacion
    Registra el estado actual del dispositivo: `conectado` = (estado_conexion ==
    'conectado') y `ultima_comunicacion` (la hora del servidor si no se envía).
    `senal`, `identificador_dispositivo` y `estado_mqtt` se aceptan pero no se
    guardan (van null en la respuesta).

GET .../estado-comunicacion
    Una lista con el último estado (o vacía si nunca se registró, o si queda
    fuera del rango de fechas pedido). `id` es el del dispositivo.

GET .../estado-comunicacion/ultima
    El último estado, o 404 si nunca se registró ninguno.

Acceso: dueño o administrador del domo, y el dispositivo debe ser de ese domo
(404 en ambos casos, para no revelar que existen).
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import Dispositivo, Usuario
from routers.dispositivos import _dispositivo_del_domo
from routers.domos import domo_accesible
from schemas import EstadoComunicacionCreate, EstadoComunicacionOut
from security import get_current_user
from servicios.mediciones import a_utc

router = APIRouter(
    prefix="/domos/{domo_id}/dispositivos/{dispositivo_id}/estado-comunicacion",
    tags=["Estado de comunicación"],
)

SIN_ESTADO = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Este dispositivo no tiene ningún estado de comunicación registrado.",
)


def _salida(dispositivo: Dispositivo) -> Optional[EstadoComunicacionOut]:
    if dispositivo.ultima_comunicacion is None:
        return None
    return EstadoComunicacionOut(
        id=dispositivo.id,
        dispositivo_id=dispositivo.id,
        estado_conexion=dispositivo.estado_conexion,
        ultima_comunicacion=dispositivo.ultima_comunicacion,
    )


@router.post("", response_model=EstadoComunicacionOut, status_code=status.HTTP_201_CREATED)
def registrar_estado_comunicacion(
    domo_id: int,
    dispositivo_id: int,
    datos: EstadoComunicacionCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    dispositivo = _dispositivo_del_domo(db, domo.id, dispositivo_id)
    dispositivo.conectado = datos.estado_conexion == "conectado"
    dispositivo.ultima_comunicacion = a_utc(datos.ultima_comunicacion or datetime.now(timezone.utc))
    db.commit()
    db.refresh(dispositivo)
    return _salida(dispositivo)


@router.get("", response_model=list[EstadoComunicacionOut])
def listar_estado_comunicacion(
    domo_id: int,
    dispositivo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
    limite: int = Query(100, ge=1, le=1000),
    fecha_desde: Optional[datetime] = None,
    fecha_hasta: Optional[datetime] = None,
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    dispositivo = _dispositivo_del_domo(db, domo.id, dispositivo_id)
    salida = _salida(dispositivo)
    if salida is None:
        return []
    momento = a_utc(salida.ultima_comunicacion)
    if (fecha_desde is not None and momento < a_utc(fecha_desde)) or (
        fecha_hasta is not None and momento > a_utc(fecha_hasta)
    ):
        return []
    return [salida]


@router.get("/ultima", response_model=EstadoComunicacionOut)
def obtener_ultimo_estado_comunicacion(
    domo_id: int,
    dispositivo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    salida = _salida(_dispositivo_del_domo(db, domo.id, dispositivo_id))
    if salida is None:
        raise SIN_ESTADO
    return salida
