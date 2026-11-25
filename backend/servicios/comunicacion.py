"""
Comunicación con las tomas y los bombillos reales: lo que la ESP32 responde a cada orden.

La ESP32 publica, RETENIDO, en domo/{id}/estado/{toma|bombillo}/{id_fisico}:
{"id_fisico", "estado": "on"|"off"|"desconocido", "verificado": bool[, "error": ...]}
(firmware/domo_nodo/domo_nodo.ino, publicarEstado). verificado:false = 3 intentos fallidos,
y entonces "estado" es el último CONFIRMADO, no el pedido.

aplicar_reporte (la llama mqtt_listener.py):
- Dispositivo inexistente o desactivado: se ignora.
- verificado true: conectado = true y ultima_comunicacion = now() (de la base, el mismo
  reloj que acciones.fecha_hora); se cierran sus alertas 'dispositivo_desconectado'
  activas. En una TOMA, si el estado reportado difiere del guardado, se actualiza, salvo
  que su última acción tenga menos de GRACIA_ORDEN_S: entonces el reporte puede ser de
  una orden anterior. No se escribe una fila en `acciones`: R-13 decide con la última
  acción y los reportes las cuentan; solo queda un log INFO.
- verificado false: `estado` NO cambia (no se inventa). conectado = false,
  ultima_comunicacion = now() y UNA alerta 'dispositivo_desconectado' activa por
  dispositivo (no se duplica).
- Bombillo: lo mismo, pero nunca toca su estado ni sus campos de luz (de esos se
  encarga servicios/iluminacion).
- RETENIDO (el que entrega el broker al suscribirse; los mensajes en vivo llegan sin la
  marca): es una foto sin fecha, quizá de una orden ya reemplazada. Solo se acepta si
  es coherente con la base (verificado y con el mismo estado); si no, se ignora.

confirmacion_de: lo que muestra la app de la última orden de una toma real
(DispositivoOut.confirmacion). reintentos_r13: las órdenes de R-13 que el listener
vuelve a enviar a una toma sin conexión.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import ACTOR_SISTEMA, Accion, Alerta, Dispositivo
from servicios import mediciones

log = logging.getLogger(__name__)

# Sin respuesta durante esto tras una orden: "pendiente"; después, "sin_confirmar".
PENDIENTE_S = 60
# Un reporte verificado que contradice la base no cambia el estado si la última acción
# tiene menos de esto: puede ser la respuesta a la orden anterior (la ESP32 tarda hasta
# ~20 s en sus 3 intentos).
GRACIA_ORDEN_S = 60
# R-13 reenvía su propia orden a una toma sin conexión como mucho cada esto.
R13_REINTENTO_S = 600

TIPO_ALERTA = "dispositivo_desconectado"
NIVEL_ALERTA = "advertencia"

PENDIENTE, SIN_CONFIRMAR, CONFIRMADA, SIN_CONEXION = "pendiente", "sin_confirmar", "confirmada", "sin_conexion"

ESTADO_DE_REPORTE = {"on": "encendido", "off": "apagado"}
MOTIVOS = {
    "sin_conexion_toma": "no respondió a la ESP32 tras 3 intentos",
    "sin_conexion_bombillo": "no respondió a la ESP32 tras 3 intentos",
    "ip_desconocida": "la ESP32 no conoce su IP (no se ha anunciado en la red)",
    "id_fisico_desconocido": "la ESP32 no lo tiene en su configuración (secrets.h)",
}


def ultima_orden(db: Session, dispositivo_id: int) -> Optional[datetime]:
    """Fecha de la última acción (encendido o apagado) del dispositivo, o None."""
    fecha = (
        db.query(Accion.fecha_hora)
        .filter(Accion.dispositivo_id == dispositivo_id)
        .order_by(Accion.fecha_hora.desc(), Accion.id.desc())
        .limit(1)
        .scalar()
    )
    return None if fecha is None else mediciones.a_utc(fecha)


def confirmacion_de(dispositivo: Dispositivo, orden: Optional[datetime], ahora: Optional[datetime] = None) -> Optional[str]:
    """De una toma real y activa: pendiente | sin_confirmar (la última orden no tiene
    respuesta posterior, hace menos o más de PENDIENTE_S) | confirmada | sin_conexion
    (respuesta posterior a la orden, buena o mala). None: simulada, bombillo,
    desactivada, o sin órdenes ni respuestas."""
    if dispositivo.tipo != "toma" or dispositivo.id_fisico is None or not dispositivo.activo:
        return None
    ahora = mediciones.a_utc(ahora or mediciones.ahora_utc())
    comunicacion = None if dispositivo.ultima_comunicacion is None else mediciones.a_utc(dispositivo.ultima_comunicacion)
    if orden is not None and (comunicacion is None or comunicacion < mediciones.a_utc(orden)):
        return PENDIENTE if ahora - mediciones.a_utc(orden) < timedelta(seconds=PENDIENTE_S) else SIN_CONFIRMAR
    if comunicacion is None:
        return None
    return CONFIRMADA if dispositivo.conectado else SIN_CONEXION


def _nombre(dispositivo: Dispositivo) -> str:
    return f"{'la toma' if dispositivo.tipo == 'toma' else 'el bombillo'} '{dispositivo.nombre}' ({dispositivo.id_fisico})"


def _alertas_activas(db: Session, dispositivo: Dispositivo) -> list[Alerta]:
    return (
        db.query(Alerta)
        .filter(
            Alerta.domo_id == dispositivo.domo_id,
            Alerta.tipo == TIPO_ALERTA,
            Alerta.id_fisico_ref == dispositivo.id_fisico,
            Alerta.estado == "activa",
        )
        .all()
    )


def _abrir_alerta(db: Session, dispositivo: Dispositivo, error: Optional[str]) -> bool:
    """Una sola alerta activa por dispositivo. True si la creó."""
    if _alertas_activas(db, dispositivo):
        return False
    motivo = MOTIVOS.get(error, f"error {error!r}" if error else "sin confirmación")
    db.add(
        Alerta(
            domo_id=dispositivo.domo_id,
            tipo=TIPO_ALERTA,
            nivel=NIVEL_ALERTA,
            mensaje=f"Sin conexión con {_nombre(dispositivo)}: {motivo}. La orden no se confirmó; "
            "su estado real puede no ser el que muestra la app.",
            id_fisico_ref=dispositivo.id_fisico,
        )
    )
    return True


def aplicar_reporte(
    db: Session, domo_id: int, tipo: str, id_fisico: str, payload: dict, retenido: bool = False, avisar_desconocido: bool = True
) -> str:
    """Aplica el reporte de la ESP32 (ver el docstring del módulo) y hace commit si cambió
    algo. Devuelve qué hizo: desconocido | desactivado | sin_verificado | retenido_ignorado
    | confirmado | sin_conexion."""
    dispositivo = (
        db.query(Dispositivo)
        .filter(Dispositivo.domo_id == domo_id, Dispositivo.id_fisico == id_fisico, Dispositivo.tipo == tipo)
        .first()
    )
    if dispositivo is None:
        if avisar_desconocido:
            log.warning("Estado de %s %r que no existe en el domo %s — se ignora.", tipo, id_fisico, domo_id)
        return "desconocido"
    if not dispositivo.activo:
        log.info("Estado de %s, que está desactivado — se ignora.", _nombre(dispositivo))
        return "desactivado"
    if not isinstance(payload.get("verificado"), bool):
        log.warning("Estado de %s sin 'verificado' — se ignora: %s", _nombre(dispositivo), payload)
        return "sin_verificado"

    verificado = payload["verificado"]
    reportado = ESTADO_DE_REPORTE.get(payload.get("estado"))
    if retenido and not (verificado and reportado == dispositivo.estado):
        log.info(
            "Estado RETENIDO de %s no coherente con la base (verificado=%s, estado=%r; en la base %s) — se ignora: "
            "puede ser de una orden ya reemplazada.",
            _nombre(dispositivo), verificado, payload.get("estado"), dispositivo.estado,
        )
        return "retenido_ignorado"

    dispositivo.ultima_comunicacion = func.now()
    if not verificado:
        dispositivo.conectado = False
        nueva = _abrir_alerta(db, dispositivo, payload.get("error"))
        db.commit()
        log.warning(
            "%s no confirmó la orden (%s): comunicación fallida, el estado guardado (%s) no se cambia%s.",
            _nombre(dispositivo).capitalize(), payload.get("error"), dispositivo.estado,
            "; alerta dispositivo_desconectado creada" if nueva else "",
        )
        return SIN_CONEXION

    dispositivo.conectado = True
    if dispositivo.tipo == "toma" and reportado is not None and reportado != dispositivo.estado:
        orden = ultima_orden(db, dispositivo.id)
        if orden is not None and mediciones.a_utc(mediciones.ahora_utc()) - orden < timedelta(seconds=GRACIA_ORDEN_S):
            log.info(
                "%s reporta %s pero su última orden es de hace menos de %s s: puede ser la respuesta a una orden "
                "anterior, el estado (%s) no se cambia.",
                _nombre(dispositivo).capitalize(), reportado, GRACIA_ORDEN_S, dispositivo.estado,
            )
        else:
            log.info(
                "%s reporta %s (verificado) y la base decía %s: se actualiza el estado (sin fila en acciones).",
                _nombre(dispositivo).capitalize(), reportado, dispositivo.estado,
            )
            dispositivo.estado = reportado
    cerradas = _alertas_activas(db, dispositivo)
    for alerta in cerradas:
        alerta.cambiar_estado("cerrada")
    db.commit()
    if cerradas:
        log.info("%s respondió: comunicación OK, alerta dispositivo_desconectado cerrada.", _nombre(dispositivo).capitalize())
    return CONFIRMADA


@dataclass(frozen=True)
class Reintento:
    dispositivo_id: int
    nombre: str
    id_fisico: str
    accion: str  # "on" | "off"


def reintentos_r13(db: Session, domo_id: int, intervalo_s: float = R13_REINTENTO_S, ahora: Optional[datetime] = None) -> list[Reintento]:
    """Tomas reales y activas cuya última orden es de R-13 (sistema), con la comunicación
    fallida después de esa orden (confirmacion sin_conexion), con el estado guardado igual
    al que R-13 pidió, y sin comunicación desde hace `intervalo_s` o más. Las órdenes de
    personas no se reintentan. El listener lleva aparte cuándo reintentó cada una."""
    ahora = mediciones.a_utc(ahora or mediciones.ahora_utc())
    tomas = (
        db.query(Dispositivo)
        .filter(
            Dispositivo.domo_id == domo_id,
            Dispositivo.tipo == "toma",
            Dispositivo.activo.is_(True),
            Dispositivo.id_fisico.is_not(None),
            Dispositivo.conectado.is_(False),
            Dispositivo.ultima_comunicacion.is_not(None),
        )
        .order_by(Dispositivo.id)
        .all()
    )
    salida = []
    for toma in tomas:
        accion = (
            db.query(Accion)
            .filter(Accion.dispositivo_id == toma.id)
            .order_by(Accion.fecha_hora.desc(), Accion.id.desc())
            .first()
        )
        if accion is None or accion.actor_tipo != ACTOR_SISTEMA or accion.estado_nuevo != toma.estado:
            continue
        if confirmacion_de(toma, accion.fecha_hora, ahora) != SIN_CONEXION:
            continue
        if ahora - mediciones.a_utc(toma.ultima_comunicacion) < timedelta(seconds=intervalo_s):
            continue
        salida.append(Reintento(toma.id, toma.nombre, toma.id_fisico, "on" if accion.accion == "encender" else "off"))
    return salida
