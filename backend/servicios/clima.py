"""
Clima de referencia de cada domo, desde Open-Meteo, para explicar la generación solar.

Son ESTIMACIONES del modelo meteorológico para las coordenadas del domo (domos.latitud y
domos.longitud), no mediciones en el domo. Open-Meteo no pide clave; sus datos son
CC BY 4.0 y la app muestra "Datos del clima: Open-Meteo.com". Un domo sin coordenadas
no tiene clima.

Ciclo (actualizar_todos), cada CLIMA_INTERVALO_S (15 min) desde el listener:
  1. lee los domos con coordenadas (una sesión corta, que se cierra antes de salir a red);
  2. por cada uno consulta `current` (urllib, timeout corto) y guarda la fila con
     INSERT … ON CONFLICT (domo_id, fecha_hora) DO NOTHING: Open-Meteo actualiza
     `current` cada 15 min, así que dos consultas en el mismo intervalo traen la misma
     fecha_hora y la segunda no duplica. Funciona igual en PostgreSQL y en SQLite.
  3. Un domo que falla (red, HTTP, JSON, base) deja un WARNING y el ciclo sigue con el
     siguiente. Nada aquí lanza hacia el hilo (ver `bucle`).

El hilo (iniciar_hilo) es independiente del trabajador de mediciones del listener: usa
su propia sesión (database.SessionLocal, no la conexión del listener) y nunca toca la
cola de MQTT, así que una consulta lenta no retrasa la telemetría ni R-13.

Una consulta a mano (para probar):  python -m servicios.clima
"""

import json
import logging
import math
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlencode

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from models import RANGOS_CLIMA, Clima, Domo
from servicios.mediciones import a_utc

log = logging.getLogger("clima")

URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
ZONA_HORARIA = "America/Bogota"
VARIABLES = ("temperature_2m", "cloud_cover", "shortwave_radiation", "precipitation", "weather_code", "is_day")
FUENTE = "open-meteo"
TIMEOUT_S = 10.0
INTERVALO_S = 900.0
# Una respuesta de `current` pesa ~1 KB; más que esto no es una respuesta válida.
TAMANO_MAXIMO = 256 * 1024

# variable de Open-Meteo -> columna de `clima`.
COLUMNA_DE = {
    "temperature_2m": "temperatura_c",
    "cloud_cover": "nubosidad_pct",
    "shortwave_radiation": "radiacion_w_m2",
    "precipitation": "precipitacion_mm",
}


class ErrorClima(Exception):
    """La consulta o su respuesta no sirven. El mensaje va al log (nunca lleva claves:
    Open-Meteo no usa)."""


def url_consulta(latitud: float, longitud: float) -> str:
    return URL_OPEN_METEO + "?" + urlencode(
        {"latitude": latitud, "longitude": longitud, "timezone": ZONA_HORARIA, "current": ",".join(VARIABLES)}
    )


def consultar(latitud: float, longitud: float, timeout: float = TIMEOUT_S) -> dict:
    """La respuesta JSON de Open-Meteo, o ErrorClima (red, timeout, HTTP o JSON)."""
    peticion = urllib.request.Request(
        url_consulta(latitud, longitud), headers={"User-Agent": "vatia-clima/1.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            cuerpo = respuesta.read(TAMANO_MAXIMO + 1)
    except urllib.error.HTTPError as e:
        raise ErrorClima(f"Open-Meteo respondió HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorClima(f"sin respuesta de Open-Meteo ({getattr(e, 'reason', e)})") from e
    if len(cuerpo) > TAMANO_MAXIMO:
        raise ErrorClima("respuesta de Open-Meteo demasiado grande")
    try:
        datos = json.loads(cuerpo)
    except ValueError as e:
        raise ErrorClima("respuesta de Open-Meteo que no es JSON") from e
    if not isinstance(datos, dict):
        raise ErrorClima("respuesta de Open-Meteo sin objeto JSON")
    return datos


def _numero(valor: object, rango: tuple[float, float]) -> Optional[float]:
    """Un número finito dentro del rango del CHECK, o None."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    if not math.isfinite(valor) or not rango[0] <= valor <= rango[1]:
        return None
    return valor


def _fecha_utc(texto: object, desfase: object) -> datetime:
    """`current.time` viene en hora local sin zona ("2026-11-22T14:15") y la zona en
    `utc_offset_seconds`. Se guarda en UTC."""
    if not isinstance(texto, str):
        raise ErrorClima("respuesta sin current.time")
    try:
        fecha = datetime.fromisoformat(texto)
    except ValueError as e:
        raise ErrorClima(f"current.time inválido: {texto[:40]!r}") from e
    if fecha.tzinfo is None:
        if isinstance(desfase, bool) or not isinstance(desfase, int) or abs(desfase) > 18 * 3600:
            raise ErrorClima("respuesta sin utc_offset_seconds válido")
        fecha = fecha.replace(tzinfo=timezone(timedelta(seconds=desfase)))
    return fecha.astimezone(timezone.utc)


def parsear(respuesta: dict) -> dict:
    """Columnas de `clima` a partir de la respuesta. Sin `current` o sin hora válida,
    ErrorClima; un valor que falta, no es número o está fuera de rango queda en None."""
    actual = respuesta.get("current") if isinstance(respuesta, dict) else None
    if not isinstance(actual, dict):
        raise ErrorClima("respuesta sin 'current'")
    datos = {"fecha_hora": _fecha_utc(actual.get("time"), respuesta.get("utc_offset_seconds"))}
    for variable, columna in COLUMNA_DE.items():
        datos[columna] = _numero(actual.get(variable), RANGOS_CLIMA[columna])
    codigo = _numero(actual.get("weather_code"), (0, 99))
    datos["codigo_clima"] = int(codigo) if codigo is not None and codigo == int(codigo) else None
    es_de_dia = actual.get("is_day")
    datos["es_de_dia"] = bool(es_de_dia) if es_de_dia in (0, 1) and not isinstance(es_de_dia, float) else None
    return datos


def _insert(db: Session):
    return postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert


def guardar(db: Session, domo_id: int, datos: dict) -> bool:
    """Inserta la fila; si ya hay una para ese domo y esa fecha_hora no hace nada.
    True si quedó una fila nueva. No hace commit: es de quien llama."""
    sentencia = (
        _insert(db)(Clima.__table__)
        .values(domo_id=domo_id, fuente=FUENTE, **datos)
        .on_conflict_do_nothing(index_elements=["domo_id", "fecha_hora"])
    )
    return db.execute(sentencia).rowcount == 1


def domos_con_coordenadas(db: Session) -> list[tuple[int, float, float]]:
    return [
        (fila.id, fila.latitud, fila.longitud)
        for fila in db.query(Domo.id, Domo.latitud, Domo.longitud)
        .filter(Domo.latitud.isnot(None), Domo.longitud.isnot(None))
        .order_by(Domo.id)
    ]


def actualizar_todos(
    fabrica_sesion: Callable[[], Session], consulta: Callable[[float, float], dict] = None
) -> dict:
    """Una vuelta por todos los domos con coordenadas. Devuelve el resumen
    {"guardados", "repetidos", "fallidos"}. Un domo que falla no detiene a los demás."""
    consulta = consulta or consultar
    resumen = {"guardados": 0, "repetidos": 0, "fallidos": 0}
    with fabrica_sesion() as db:
        domos = domos_con_coordenadas(db)
    for domo_id, latitud, longitud in domos:
        try:
            # La red va fuera de la sesión: no se tiene una conexión abierta esperando.
            datos = parsear(consulta(latitud, longitud))
            with fabrica_sesion() as db:
                nueva = guardar(db, domo_id, datos)
                db.commit()
        except ErrorClima as e:
            resumen["fallidos"] += 1
            log.warning("Clima del domo %s no disponible: %s. Se reintenta en el próximo ciclo.", domo_id, e)
            continue
        except Exception as e:
            resumen["fallidos"] += 1
            log.warning("Clima del domo %s no se guardó (%s: %s). Se reintenta en el próximo ciclo.",
                        domo_id, type(e).__name__, e)
            continue
        resumen["guardados" if nueva else "repetidos"] += 1
        if nueva:
            log.info("Clima del domo %s guardado (%s UTC, nubosidad %s %%, radiación %s W/m²).",
                     domo_id, datos["fecha_hora"].strftime("%Y-%m-%d %H:%M"),
                     datos["nubosidad_pct"], datos["radiacion_w_m2"])
    return resumen


def bucle(fabrica_sesion: Callable[[], Session], intervalo_s: float, detener: threading.Event) -> None:
    """Consulta al arrancar y luego cada intervalo_s, hasta que `detener` se active.
    Ningún error termina el hilo: queda en el log y se espera al siguiente ciclo."""
    while not detener.is_set():
        try:
            actualizar_todos(fabrica_sesion)
        except Exception as e:  # p. ej. la base no responde al listar los domos
            log.warning("Ciclo de clima fallido (%s: %s). Se reintenta en %g s.", type(e).__name__, e, intervalo_s)
        detener.wait(intervalo_s)


def iniciar_hilo(fabrica_sesion: Callable[[], Session], intervalo_s: float = INTERVALO_S) -> threading.Event:
    """Arranca el hilo daemon del clima y devuelve el Event que lo detiene."""
    detener = threading.Event()
    threading.Thread(
        target=bucle, args=(fabrica_sesion, intervalo_s, detener), name="clima-open-meteo", daemon=True
    ).start()
    return detener


def antiguedad_s(fila: Clima, ahora: Optional[datetime] = None) -> int:
    """Segundos desde la hora de la estimación (nunca negativos)."""
    ahora = ahora or datetime.now(timezone.utc)
    return max(0, int((ahora - a_utc(fila.fecha_hora)).total_seconds()))


if __name__ == "__main__":
    import database

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    print(actualizar_todos(database.SessionLocal))
