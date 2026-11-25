"""
Estado de la recalibración guiada del SoC, EN MEMORIA (sin tabla: no cambia el esquema).

La ESP32 publica en `domo/{id}/estado/soc` (RETENIDO) el estado de la sesión
(firmware/domo_nodo/soc_recalibracion.cpp):

    {"estado": "inactiva|esperando|lista|aplicada|cancelada|vencida|rechazada",
     "minutos_ok": 3.5, "requeridos": 15, "motivo": "hay sol: …",
     "soc_actual": 100.0, "soc_propuesto": 58.0, "voltaje": 12.41, "t": 1795000000}

El cliente MQTT del backend (mqtt_publisher.py, uno por proceso de uvicorn, con loop_start)
se suscribe a `domo/+/estado/soc` y entrega cada mensaje a `registrar`. Como el mensaje es
retenido, al (re)conectarse el broker entrega enseguida el último: el backend no necesita
guardarlo en la base. Al arrancar, la ESP32 publica "inactiva", que reemplaza un "esperando" o
"lista" retenido de antes del reinicio.

`estado_de` agrega dos estados solo del backend:
- "sin_sesion": nunca llegó un estado de ese domo (ESP32 con firmware viejo o sin broker);
- "interrumpida": un "esperando" o "lista" sin noticias desde hace más de SIN_NOTICIAS_S (la
  ESP32 lo republica cada 30 s mientras la sesión está activa): se reinició o perdió la red.
"""

import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, get_args

from schemas import EstadoRecalibracionSoc

TEMA_ESTADO_SOC = "domo/+/estado/soc"
SIN_NOTICIAS_S = 90
ACTIVOS = ("esperando", "lista")
_CAMPOS_NUMERICOS = ("minutos_ok", "requeridos", "soc_actual", "soc_propuesto", "voltaje")
_LARGO_MAXIMO_MOTIVO = 200
# Una "t" anterior a esto no es una hora NTP válida.
_HORA_MINIMA_VALIDA = 1_700_000_000

log = logging.getLogger("estado_soc")

_lock = threading.Lock()
_ultimos: dict[int, tuple[dict, datetime]] = {}


def domo_del_tema(tema: str) -> Optional[int]:
    """El domo_id de `domo/{id}/estado/soc`, o None si el tema no es ese."""
    partes = tema.split("/")
    if len(partes) != 4 or partes[0] != "domo" or partes[2] != "estado" or partes[3] != "soc":
        return None
    try:
        return int(partes[1])
    except ValueError:
        return None


def _numero(valor: object) -> Optional[float]:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    return float(valor)


def interpretar(payload: bytes) -> Optional[dict]:
    """El payload validado, o None si no se entiende (JSON inválido o estado desconocido)."""
    try:
        datos = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(datos, dict) or datos.get("estado") not in get_args(EstadoRecalibracionSoc):
        return None
    salida = {"estado": datos["estado"]}
    for campo in _CAMPOS_NUMERICOS:
        salida[campo] = _numero(datos.get(campo))
    motivo = datos.get("motivo")
    salida["motivo"] = motivo[:_LARGO_MAXIMO_MOTIVO] if isinstance(motivo, str) and motivo else None
    t = _numero(datos.get("t"))
    salida["t"] = datetime.fromtimestamp(t, timezone.utc) if t is not None and t > _HORA_MINIMA_VALIDA else None
    return salida


def registrar(tema: str, payload: bytes, ahora: Optional[datetime] = None) -> bool:
    """Guarda el último estado del domo del tema. False (y un aviso) si no se entiende."""
    domo_id = domo_del_tema(tema)
    datos = interpretar(payload) if domo_id is not None else None
    if datos is None:
        log.warning("Estado de recalibración del SoC no válido en '%s': %r", tema, payload[:200])
        return False
    with _lock:
        _ultimos[domo_id] = (datos, ahora or datetime.now(timezone.utc))
    log.info("Recalibración del SoC, domo %s: %s (%s)", domo_id, datos["estado"], datos["motivo"] or "-")
    return True


def estado_de(domo_id: int, ahora: Optional[datetime] = None) -> dict:
    """Respuesta de GET /domos/{id}/soc-recalibracion (sin domo_id)."""
    ahora = ahora or datetime.now(timezone.utc)
    with _lock:
        ultimo = _ultimos.get(domo_id)
    if ultimo is None:
        return {"estado": "sin_sesion"}
    datos, recibido = ultimo
    actualizado = datos["t"] or recibido
    salida = {campo: valor for campo, valor in datos.items() if campo != "t"}
    salida["actualizado"] = actualizado
    if datos["estado"] in ACTIVOS and ahora - actualizado > timedelta(seconds=SIN_NOTICIAS_S):
        salida["estado"] = "interrumpida"
        salida["motivo"] = "Sin noticias de la ESP32: se reinició o perdió la conexión. Inicia otra vez."
    return salida


def limpiar() -> None:
    """Para las pruebas."""
    with _lock:
        _ultimos.clear()
