"""
Recalibración GUIADA del SoC desde la app (solo el dueño del domo).

POST /domos/{domo_id}/soc-recalibracion   {"accion": "iniciar" | "confirmar" | "cancelar"}
    Publica la acción en domo/{id}/comando/soc (mqtt_publisher.publicar_comando_soc:
    qos 1, NUNCA retenido) y responde 202: la ESP32 la ejecuta y publica el resultado en
    domo/{id}/estado/soc. El dueño no manda un valor de SoC (un campo extra es 422): lo mide
    la ESP32 en reposo con la tabla de voltaje y el dueño solo lo confirma. Así nadie puede
    dejar el SoC en un valor inventado con el que R-13 decidiría mal. Broker caído: 503.

GET /domos/{domo_id}/soc-recalibracion
    El último estado que publicó la ESP32 (servicios/estado_soc.py, en memoria, sin tabla).

Acceso: el de las acciones del dueño (routers.domos.domo_del_dueno): un domo ajeno es 404 y
quien es administrador de ESE domo recibe 403. No se guarda en `acciones` (su FK es a
`dispositivos`): queda en el log del backend con el usuario y la acción.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import mqtt_publisher
from database import get_db
from models import Usuario
from routers.domos import domo_del_dueno
from schemas import SocRecalibracionAccionOut, SocRecalibracionIn, SocRecalibracionOut
from security import get_current_user
from servicios import estado_soc

router = APIRouter(prefix="/domos/{domo_id}/soc-recalibracion", tags=["Recalibración del SoC"])

log = logging.getLogger("soc_recalibracion")


@router.post("", response_model=SocRecalibracionAccionOut, status_code=status.HTTP_202_ACCEPTED)
def pedir_recalibracion(
    domo_id: int,
    datos: SocRecalibracionIn,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_del_dueno(db, domo_id, usuario_actual)
    # Antes de publicar: así la respuesta de la ESP32 (estado/soc) ya tiene quién la reciba.
    mqtt_publisher.escuchar_estado_soc(espera_s=0)
    enviado = mqtt_publisher.publicar_comando_soc(domo.id, datos.accion)
    log.info(
        "Recalibración del SoC: domo %s, usuario %s (%s), acción %s -> %s",
        domo.id, usuario_actual.id, usuario_actual.correo, datos.accion, "enviada" if enviado else "NO enviada",
    )
    if not enviado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo enviar la orden a la ESP32 (sin conexión con el broker MQTT). Inténtalo de nuevo.",
        )
    return {"domo_id": domo.id, "accion": datos.accion, "enviado": True}


@router.get("", response_model=SocRecalibracionOut)
def obtener_recalibracion(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_del_dueno(db, domo_id, usuario_actual)
    mqtt_publisher.escuchar_estado_soc()
    return {"domo_id": domo.id, **estado_soc.estado_de(domo.id)}
