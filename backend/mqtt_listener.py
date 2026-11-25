"""
Listener MQTT del backend — escucha telemetría real (del ESP32, o de
`mosquitto_pub` durante pruebas) y la guarda en la tabla `mediciones` (modelo v2).

Es un camino de entrada ADICIONAL, no un reemplazo: los endpoints POST de
R-6/R-7/R-8/R-9 (routers/lecturas_*.py) guardan con la misma función,
servicios/mediciones.guardar_medicion. El contrato MQTT (temas y campos) no cambió.

Modelo v2: una fila por domo y bloque de 10 s. Cada mensaje es un
INSERT … ON CONFLICT (domo_id, fecha_hora) DO UPDATE sobre el bloque de su
`fecha_hora` (o de la hora del servidor): reemplaza los valores instantáneos de
su tema y SUMA la energía del intervalo (energia_generada, energia_consumida).
Las columnas de los otros temas no se tocan. Se ignoran, con un aviso en el log,
`estado_generacion` (se calcula de la potencia), `sensor_id` y los campos
desconocidos. Una lectura de consumo con `dispositivo_id` o de sensores con una
variable distinta de frecuencia/factor_potencia se descarta: v2 no tiene dónde
guardarla.

Corre como proceso independiente, en su propia terminal, separado de
uvicorn (que sigue sirviendo la API REST normalmente):

    python mqtt_listener.py

Requiere el broker Mosquitto corriendo (ver docker-compose.yml en la raíz
del proyecto: `docker compose up`) y las variables MQTT_BROKER_HOST/
MQTT_BROKER_PORT en backend/.env.

Temas (MAPEO_TIPOS más abajo; las columnas, en servicios/mediciones.CAMPOS_POR_TEMA):

    domo/{domo_id}/telemetria/fotovoltaica  → mediciones: voltaje_fv_v, corriente_fv_a, potencia_fv_w, energia_fv_wh
    domo/{domo_id}/telemetria/consumo       → mediciones: voltaje_ac_v, corriente_ac_a, potencia_consumo_w, energia_consumo_wh
    domo/{domo_id}/telemetria/bateria       → mediciones: soc_pct, estado_bateria, voltaje_bateria_v, … temperatura_bateria_c
    domo/{domo_id}/telemetria/sensores      → mediciones: frecuencia_hz o factor_potencia, según variable_medida

Se suscribe con el patrón `domo/+/telemetria/+`: el primer comodín es
`domo_id`, el segundo es el tipo (una de las cuatro claves de
MAPEO_TIPOS). Cualquier otro tipo, o un tema con otra forma, se loguea
como error y se descarta sin interrumpir el proceso.

Nota — esto difiere del convenio de topics documentado en
docs/Requisitos/Variables.md (sección 13: prefijo "glamping/", último
segmento `dispositivo_id` en vez de tipo). Se implementa con el esquema
`domo/{domo_id}/telemetria/{tipo}` porque es lo que se pidió explícitamente
para este listener. Si el proyecto adopta formalmente el convenio de
Variables.md más adelante, este archivo es el único lugar que hace falta
tocar (el mapeo vive todo en MAPEO_TIPOS y _procesar_mensaje).

Estado del bombillo: también se suscribe a `domo/+/estado/bombillo/+`, donde el
ESP32 publica (retenido) lo que el bombillo CONFIRMÓ: {"id_fisico", "estado",
"verificado", "brillo", "modo", "temperatura", "color": {"h", "s"}, "error"}. Con eso
se actualizan en `dispositivos` modo_luz, brillo, temperatura_color (la más cercana de
calida/neutra/fria) y color ("#rrggbb" del tono), solo los campos que vengan y se
entiendan (servicios/iluminacion.campos_del_estado): un firmware viejo, sin modo ni
color, no borra nada. No se toca `estado` (encendido/apagado): eso sigue siendo la
decisión de las personas y de R-13 (historial R-16 y primacía humana). Un bombillo que
no existe en ese domo, o un aviso "id_fisico_desconocido", se ignoran con un aviso.

Estado de las tomas y comunicación: también se suscribe a `domo/+/estado/toma/+`. Con
el mismo mensaje (y el del bombillo) se registra la comunicación del dispositivo
(conectado, ultima_comunicacion) y se abre o cierra su alerta 'dispositivo_desconectado'.
Con verificado true una toma puede cambiar de `estado`; con false, nunca. Los mensajes
RETENIDOS (marca retain del broker al suscribirse) solo cuentan si son coherentes con la
base. Todo en servicios/comunicacion.py.

Recalibración guiada del SoC: también se suscribe a `domo/+/estado/soc` (retenido, lo
publica el ESP32) y SOLO lo registra en el log (INFO). No se guarda en la base: `acciones`
tiene FK a `dispositivos`. La API conoce ese estado con su propia suscripción
(mqtt_publisher.py y servicios/estado_soc.py).

Reintentos de R-13: tras cada evaluación automática, las órdenes PROPIAS de R-13 a una
toma con la comunicación fallida se reenvían, como mucho cada R13_REINTENTO_S (600 s)
por toma, sin fila en acciones ni cambio de estado. Las de personas no se reintentan.

Sin autenticación JWT a propósito: este tráfico es de infraestructura
interna (ESP32 → broker → backend), no de un usuario con sesión — no hay
ningún `usuario_actual` en absoluto en este flujo, por diseño (a
diferencia de todos los endpoints REST del proyecto, que sí la exigen).

Validación de domo_id: si el domo no existe en la base de datos, el
mensaje se loguea como error y se descarta — nunca se crea un domo
implícitamente a partir de telemetría entrante.

Valores inválidos: una lectura no se pierde por un dato fuera de catálogo o
de rango, porque sin lecturas de batería R-13 no se evalúa justo cuando puede
hacer falta. Un SoC entre -5 y 105 se limita a 0–100; fuera de ese margen, o si
no es un número finito, se guarda NULL. Cualquier otro valor numérico fuera de
schemas.LIMITES_TELEMETRIA, o que no sea un número, también se guarda como NULL,
igual que un estado desconocido. Todo esto deja un WARNING en el log. La API REST, en
cambio, responde 422 (schemas.py). Los CHECK de `mediciones` usan esos mismos
límites, así que lo saneado nunca los incumple.

Evaluación automática de R-13: después de insertar con éxito una lectura
de BATERÍA, se ejecuta el algoritmo de supervivencia para ese domo
(servicios/control_energetico.py — la misma función que usa
POST .../control-energetico/evaluar, con las mismas reglas, historial y
publicación MQTT posterior al commit). Dos protecciones:

- R13_AUTO (por defecto "true"): con "false" no se evalúa nada.
- R13_AUTO_INTERVALO_S (por defecto 30): como máximo una evaluación por
  domo cada ese número de segundos; las lecturas que llegan antes solo se
  insertan. Se mide con time.monotonic() y el instante se registra ANTES
  de evaluar, así que una evaluación que falla también consume su turno
  (no se reintenta en cada lectura llenando el log de errores).

Cualquier error de la evaluación se registra en el log y no detiene el
listener ni afecta a la lectura, que ya quedó guardada. Si no hay un SoC actual
(servicios/control_energetico.soc_actual), R-13 no decide y el motivo queda en el
log una sola vez por domo, hasta que cambie o vuelva a haber SoC. Una toma que R-13
respeta por primacía humana (la encendió una persona, DB-4) se anuncia una vez por
toma y por encendido; un corte por la reserva lleva "motivo=reserva".

Recepción desacoplada del guardado: el callback de MQTT (`on_message`)
solo ENCOLA el mensaje y vuelve de inmediato; un único hilo trabajador lo
saca de la cola, lo guarda y, si corresponde, ejecuta R-13. Una sola cola
y un solo trabajador conservan el orden de llegada (las lecturas de un
mismo domo se guardan en el orden en que llegaron). Así una base de datos
lenta (Supabase por Internet: ~150 ms por viaje) ya no frena la recepción
de MQTT. Cada mensaje procesado deja en el log cuánto tardó, cuánto
esperó en la cola y cuántos quedan en espera.

Viajes a la base de datos por lectura, reducidos a BEGIN + INSERT … ON CONFLICT + COMMIT:
- Conexión dedicada del listener (_engine_listener) que se reutiliza entre
  mensajes, sin pool_pre_ping (que costaba un SELECT 1 por mensaje). Si la
  conexión se cayó, el primer uso falla, SQLAlchemy la invalida y la
  inserción se reintenta UNA vez con una conexión nueva.
- La existencia de cada domo se verifica una sola vez y queda en caché
  (_domos_conocidos). Si un domo se borrara después, la inserción falla
  por llave foránea y se saca de la caché.
R-13 usa su propia sesión nueva en cada evaluación, para leer siempre el
estado actual de los dispositivos (nada cacheado de mensajes anteriores).

Clima de referencia (servicios/clima.py): un hilo aparte consulta Open-Meteo para cada
domo con coordenadas al arrancar y luego cada CLIMA_INTERVALO_S (por defecto 900 s).
Usa database.SessionLocal (no la conexión del listener) y no toca la cola, así que una
consulta lenta o fallida no retrasa la telemetría ni R-13; un fallo solo deja un WARNING.
- CLIMA_AUTO (por defecto "true"): con "false" no se consulta el clima.
- CLIMA_INTERVALO_S (por defecto 900, mínimo 60): segundos entre consultas.
"""

import json
import logging
import math
import os
import queue
import threading
import time
from datetime import datetime
from typing import Optional, get_args

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

import database
from models import Dispositivo, Domo
from mqtt_publisher import publicar_comando_toma
from schemas import LIMITES_TELEMETRIA, EstadoBateriaValor, sin_valores_diminutos
from servicios.control_energetico import EvaluacionNoPosible, evaluar_control_energetico
from servicios.iluminacion import campos_del_estado
from servicios.mediciones import MedicionInvalida, guardar_medicion
from servicios import clima, comunicacion, estado_soc

load_dotenv()

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
TOPIC_SUSCRIPCION = "domo/+/telemetria/+"
TOPIC_ESTADO_BOMBILLO = "domo/+/estado/bombillo/+"
TOPIC_ESTADO_TOMA = "domo/+/estado/toma/+"
TOPIC_ESTADO_SOC = estado_soc.TEMA_ESTADO_SOC

R13_AUTO = os.getenv("R13_AUTO", "true").strip().lower() not in ("false", "0", "no")
R13_AUTO_INTERVALO_S = float(os.getenv("R13_AUTO_INTERVALO_S", "30"))
R13_REINTENTO_S = float(os.getenv("R13_REINTENTO_S", str(comunicacion.R13_REINTENTO_S)))

CLIMA_AUTO = os.getenv("CLIMA_AUTO", "true").strip().lower() not in ("false", "0", "no")
CLIMA_INTERVALO_S = max(60.0, float(os.getenv("CLIMA_INTERVALO_S", str(clima.INTERVALO_S))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_listener")

# domo_id -> time.monotonic() de su última evaluación automática de R-13.
_ultima_evaluacion_r13: dict[int, float] = {}
# domo_id -> motivo por el que R-13 no se evaluó la última vez. El motivo se registra
# una sola vez (no cada 30 s mientras dure), y otra vez solo si cambia.
_motivo_r13_no_evaluado: dict[int, str] = {}
# domo_id -> {toma_id: desde} de las tomas que R-13 ya anunció en el log como respetadas
# por primacía humana (DB-4). Se anuncia una vez por toma y por encendido, no cada 30 s.
_primacia_anunciada: dict[int, dict[int, object]] = {}
# dispositivo_id -> time.monotonic() del último reintento de R-13 a esa toma.
_ultimo_reintento_r13: dict[int, float] = {}

# Conexión propia del listener (ver docstring): un solo hilo trabajador la
# usa, así que basta un pool mínimo; sin pre_ping, con reciclado periódico.
_engine_listener = create_engine(
    database.DATABASE_URL, pool_size=1, max_overflow=1, pool_pre_ping=False, pool_recycle=1800
)
SesionListener = sessionmaker(bind=_engine_listener, autoflush=False, expire_on_commit=False)

# Domos cuya existencia ya se verificó (solo los usa el hilo trabajador).
_domos_conocidos: set[int] = set()

# (topic, payload, retenido, instante de llegada) — la llena on_message, la vacía el
# hilo trabajador. None es la señal de fin.
_cola: "queue.Queue[Optional[tuple[str, bytes, bool, float]]]" = queue.Queue()

# tipo (4º segmento del tema) -> (campos que se guardan, campos que se aceptan sin
# guardar: no generan aviso). `fecha_hora` se maneja aparte en _insertar_lectura:
# es opcional y, si falta, se usa la hora del servidor.
MAPEO_TIPOS: dict[str, tuple[set[str], set[str]]] = {
    "fotovoltaica": (
        {"voltaje", "corriente", "potencia_generada", "energia_generada"},
        {"estado_generacion", "sensor_id"},
    ),
    "consumo": (
        {"voltaje", "corriente", "potencia_consumida", "energia_consumida"},
        {"dispositivo_id", "sensor_id"},
    ),
    "bateria": (
        {"voltaje", "corriente", "potencia", "estado_carga_soc", "temperatura", "estado_bateria"},
        {"sensor_id"},
    ),
    "sensores": (
        {"variable_medida", "valor"},
        {"tipo_sensor", "unidad_medida", "estado", "sensor_id"},
    ),
}

ESTADOS_PERMITIDOS: dict[str, tuple[str, ...]] = {
    "estado_bateria": get_args(EstadoBateriaValor),
}

# Fuera de este margen el SoC no se limita a 0–100: se considera dato absurdo.
SOC_MIN_TOLERADO = -5.0
SOC_MAX_TOLERADO = 105.0


def _numero_o_nan(valor: object) -> float:
    if isinstance(valor, bool):
        return math.nan
    try:
        return float(valor)
    except (TypeError, ValueError, OverflowError):
        return math.nan


def _limitar_a_rango(domo_id: int, campo: str, valor: object) -> Optional[float]:
    """El valor si es un número dentro de LIMITES_TELEMETRIA, o None. La
    comparación también descarta NaN."""
    minimo, maximo = LIMITES_TELEMETRIA[campo]
    numero = _numero_o_nan(valor)
    if not minimo <= numero <= maximo:
        log.warning("%s fuera de rango (%r) en domo %s — se guarda NULL.", campo, valor, domo_id)
        return None
    return sin_valores_diminutos(numero)


def _limitar_soc(domo_id: int, valor: object) -> Optional[float]:
    """SoC dentro de 0–100 si el desvío es pequeño (ruido del sensor), o None
    si el dato es absurdo: fuera de SOC_MIN_TOLERADO–SOC_MAX_TOLERADO o no es
    un número finito. Limitar un valor así haría que R-13 reactivara o cortara
    tomas por un dato falso; con None, esa lectura no mueve ninguna toma."""
    soc = _numero_o_nan(valor)
    if not SOC_MIN_TOLERADO <= soc <= SOC_MAX_TOLERADO:
        log.warning("estado_carga_soc inválido (%r) en domo %s — se guarda NULL.", valor, domo_id)
        return None

    limitado = min(100.0, max(0.0, soc))
    if limitado != soc:
        log.warning("estado_carga_soc fuera de rango (%r) en domo %s — se guarda %s.", valor, domo_id, limitado)
    return sin_valores_diminutos(limitado)


def _sanear_valores(domo_id: int, valores: dict) -> None:
    """Ajusta en sitio lo que la base de datos rechazaría, para no perder la
    lectura completa por un solo dato (ver docstring del módulo)."""
    for campo, permitidos in ESTADOS_PERMITIDOS.items():
        if valores.get(campo) is not None and valores[campo] not in permitidos:
            log.warning("%s desconocido (%r) en domo %s — se guarda NULL.", campo, valores[campo], domo_id)
            valores[campo] = None

    for campo in LIMITES_TELEMETRIA:
        if valores.get(campo) is None:
            continue
        if campo == "estado_carga_soc":
            valores[campo] = _limitar_soc(domo_id, valores[campo])
        else:
            valores[campo] = _limitar_a_rango(domo_id, campo, valores[campo])


def _parsear_fecha_hora(valor: object) -> Optional[datetime]:
    """Convierte el `fecha_hora` del payload (string ISO 8601) a datetime.
    None si no vino, si vino vacío, o si no se pudo parsear (en ese caso
    ya se logueó el error y la lectura sigue adelante sin fecha_hora
    explícita — la base de datos le pone la hora del servidor)."""
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        log.error("fecha_hora inválida en el payload (%r) — se ignora, usará la hora del servidor.", valor)
        return None


def _toca_evaluar_r13(domo_id: int, ahora: float) -> bool:
    """Limitador por domo: True (y registra `ahora` como la última
    evaluación) si pasaron al menos R13_AUTO_INTERVALO_S segundos desde la
    anterior de ESE domo, o si nunca se evaluó."""
    ultima = _ultima_evaluacion_r13.get(domo_id)
    if ultima is not None and ahora - ultima < R13_AUTO_INTERVALO_S:
        return False
    _ultima_evaluacion_r13[domo_id] = ahora
    return True


def _evaluar_r13_automatico(domo_id: int) -> None:
    """Ejecuta R-13 para el domo tras una lectura de batería, respetando
    R13_AUTO y el limitador. Nunca lanza: cualquier error queda en el log."""
    if not R13_AUTO or not _toca_evaluar_r13(domo_id, time.monotonic()):
        return

    inicio = time.perf_counter()
    db = SesionListener()
    try:
        resultado = evaluar_control_energetico(db, domo_id)
        duracion = time.perf_counter() - inicio
        if _motivo_r13_no_evaluado.pop(domo_id, None) is not None:
            log.info("R-13 automático (domo %s): vuelve a haber SoC actual, se evalúa de nuevo.", domo_id)
        if resultado.acciones:
            for accion in resultado.acciones:
                log.info(
                    "R-13 automático (domo %s, SoC %s): %s '%s' (umbral %s%s) — %.2f s.",
                    domo_id, resultado.estado_carga_soc, accion.accion_realizada, accion.nombre, accion.umbral_aplicado,
                    "; motivo=reserva: la había encendido una persona y la batería bajó de la reserva"
                    if accion.motivo == "reserva" else "",
                    duracion,
                )
        else:
            log.info("R-13 automático (domo %s, SoC %s): sin cambios — %.2f s.", domo_id, resultado.estado_carga_soc, duracion)
        _anunciar_primacias(domo_id, resultado)
    except EvaluacionNoPosible as exc:
        motivo = str(exc)
        if _motivo_r13_no_evaluado.get(domo_id) != motivo:
            _motivo_r13_no_evaluado[domo_id] = motivo
            log.warning("R-13 automático (domo %s) no decide: %s (no se repite mientras siga igual)", domo_id, motivo)
    except Exception:
        db.rollback()
        log.exception("Error en la evaluación automática de R-13 para el domo %s — el listener sigue.", domo_id)
    finally:
        db.close()
    _reintentar_ordenes_r13(domo_id)


def _reintentar_ordenes_r13(domo_id: int) -> None:
    """Reenvía las órdenes propias de R-13 a las tomas con la comunicación fallida (ver
    comunicacion.reintentos_r13), como mucho cada R13_REINTENTO_S por toma. Sin fila en
    acciones ni cambio de estado. Nunca lanza."""
    db = SesionListener()
    try:
        ahora = time.monotonic()
        for r in comunicacion.reintentos_r13(db, domo_id, R13_REINTENTO_S):
            ultimo = _ultimo_reintento_r13.get(r.dispositivo_id)
            if ultimo is not None and ahora - ultimo < R13_REINTENTO_S:
                continue
            _ultimo_reintento_r13[r.dispositivo_id] = ahora
            log.warning(
                "R-13 automático (domo %s): REINTENTA '%s' (%s) -> %s: su orden no se confirmó (sin conexión). "
                "Sin nueva acción ni cambio de estado; el siguiente reintento, en %g min como mínimo si sigue fallando.",
                domo_id, r.nombre, r.id_fisico, r.accion, R13_REINTENTO_S / 60,
            )
            publicar_comando_toma(domo_id=domo_id, accion=r.accion, id_fisico=r.id_fisico, origen="r13")
    except Exception:
        log.exception("Error revisando los reintentos de R-13 del domo %s — el listener sigue.", domo_id)
    finally:
        db.close()


def _anunciar_primacias(domo_id: int, resultado) -> None:
    """Deja en el log las tomas que R-13 respeta por primacía humana, una vez por toma
    y por encendido (`encendida_desde`) mientras dure."""
    anunciadas = _primacia_anunciada.setdefault(domo_id, {})
    for toma in resultado.respetadas_por_primacia_humana:
        if anunciadas.get(toma.dispositivo_id) == toma.encendida_desde:
            continue
        anunciadas[toma.dispositivo_id] = toma.encendida_desde
        log.info(
            "R-13 automático (domo %s, SoC %s): respeta '%s', encendida a mano (su umbral de apagado es %s %%): "
            "no la corta hasta que la batería llegue a %s %%, salvo que baje de la reserva (%s %%). "
            "No se repite mientras dure.",
            domo_id, resultado.estado_carga_soc, toma.nombre, toma.umbral_apagado_soc, toma.hasta_soc, toma.reserva_soc,
        )


def _guardar_lectura(domo_id: int, tipo: str, valores: dict, fecha_hora: Optional[datetime]) -> Optional[datetime]:
    """Un intento de guardado: inicio del bloque donde quedó, o None si el domo no
    existe. Lanza los errores de base de datos a quien llama."""
    with SesionListener() as db:
        if domo_id not in _domos_conocidos:
            if db.get(Domo, domo_id) is None:
                log.error("Domo %s no existe — se descarta la lectura de '%s'.", domo_id, tipo)
                return None
            _domos_conocidos.add(domo_id)

        try:
            bloque = guardar_medicion(db, domo_id, tipo, valores, fecha_hora)
            db.commit()
        except IntegrityError:
            # Un domo borrado después de cachearlo: se vuelve a verificar.
            _domos_conocidos.discard(domo_id)
            raise
        return bloque


def _insertar_lectura(domo_id: int, tipo: str, campos: set[str], aceptados: set[str], payload: dict) -> bool:
    """Guarda la lectura en su bloque; devuelve True si quedó guardada."""
    if tipo == "consumo" and payload.get("dispositivo_id") is not None:
        log.error(
            "Lectura de consumo con dispositivo_id (domo %s) — se descarta: v2 guarda solo el consumo del domo.", domo_id
        )
        return False

    valores = {campo: payload[campo] for campo in campos if campo in payload}
    _sanear_valores(domo_id, valores)

    ignorados = set(payload) - campos - aceptados - {"fecha_hora"}
    if ignorados:
        log.warning(
            "Campos no reconocidos en payload de '%s' (domo %s), se ignoran: %s",
            tipo, domo_id, sorted(ignorados),
        )

    fecha_hora = _parsear_fecha_hora(payload.get("fecha_hora"))

    inicio = time.perf_counter()
    for intento in (1, 2):
        try:
            bloque = _guardar_lectura(domo_id, tipo, valores, fecha_hora)
            break
        except MedicionInvalida as exc:
            log.error("Lectura de '%s' descartada (domo %s): %s", tipo, domo_id, exc)
            return False
        except DBAPIError as exc:
            # Conexión caída (p. ej. cerrada por inactividad): SQLAlchemy ya
            # la invalidó; se reintenta una vez con una conexión nueva.
            if exc.connection_invalidated and intento == 1:
                log.warning("Conexión con la base de datos perdida — reintentando la lectura de '%s' (domo %s).", tipo, domo_id)
                continue
            log.exception("Error insertando lectura de '%s' para domo %s — mensaje descartado.", tipo, domo_id)
            return False
        except Exception:
            log.exception("Error insertando lectura de '%s' para domo %s — mensaje descartado.", tipo, domo_id)
            return False

    if bloque is None:
        return False
    log.info(
        "Guardado en mediciones: tema=%s domo_id=%s bloque=%s campos=%s — %.2f s",
        tipo, domo_id, bloque.isoformat(), sorted(valores), time.perf_counter() - inicio,
    )
    return True


def _guardar_estado_bombillo(domo_id: int, id_fisico: str, campos: dict) -> None:
    """Un intento: aplica `campos` al bombillo real de ese domo. Lanza los errores de
    base de datos a quien llama."""
    with SesionListener() as db:
        bombillo = (
            db.query(Dispositivo)
            .filter(Dispositivo.domo_id == domo_id, Dispositivo.id_fisico == id_fisico, Dispositivo.tipo == "bombillo")
            .first()
        )
        if bombillo is None:
            log.warning("Estado de un bombillo que no existe en el domo %s (id_fisico %r) — se ignora.", domo_id, id_fisico)
            return
        distintos = {campo: valor for campo, valor in campos.items() if getattr(bombillo, campo) != valor}
        if not distintos:
            return
        for campo, valor in distintos.items():
            setattr(bombillo, campo, valor)
        db.commit()
        log.info("Bombillo %r (domo %s) actualizado desde su estado: %s", id_fisico, domo_id, distintos)


def _con_reintento(que: str, domo_id: int, guardar) -> None:
    """Ejecuta `guardar()` con un reintento si la conexión con la base se cayó. Nunca lanza."""
    for intento in (1, 2):
        try:
            guardar()
            return
        except DBAPIError as exc:
            if exc.connection_invalidated and intento == 1:
                log.warning("Conexión con la base de datos perdida — reintentando %s.", que)
                continue
            log.exception("Error guardando %s (domo %s) — mensaje descartado.", que, domo_id)
            return
        except Exception:
            log.exception("Error guardando %s (domo %s) — mensaje descartado.", que, domo_id)
            return


def _guardar_comunicacion(domo_id: int, tipo: str, id_fisico: str, payload: dict, retenido: bool) -> None:
    with SesionListener() as db:
        comunicacion.aplicar_reporte(db, domo_id, tipo, id_fisico, payload, retenido, avisar_desconocido=tipo == "toma")


def _procesar_estado_bombillo(domo_id: int, id_fisico: str, payload: dict, retenido: bool = False) -> None:
    if payload.get("error") == "id_fisico_desconocido":
        log.warning("El ESP32 no conoce el bombillo %r del domo %s (no está en su secrets.h) — se ignora.", id_fisico, domo_id)
        return
    campos = campos_del_estado(payload)
    if campos:
        _con_reintento(
            f"el estado del bombillo {id_fisico!r}", domo_id, lambda: _guardar_estado_bombillo(domo_id, id_fisico, campos)
        )
    # La comunicación y su alerta; nunca el estado ni la luz del bombillo.
    if "verificado" in payload:
        _con_reintento(
            f"la comunicación del bombillo {id_fisico!r}", domo_id,
            lambda: _guardar_comunicacion(domo_id, "bombillo", id_fisico, payload, retenido),
        )


def _procesar_estado_toma(domo_id: int, id_fisico: str, payload: dict, retenido: bool = False) -> None:
    _con_reintento(
        f"el estado de la toma {id_fisico!r}", domo_id,
        lambda: _guardar_comunicacion(domo_id, "toma", id_fisico, payload, retenido),
    )


def _payload_json(topic: str, payload_bytes: bytes) -> Optional[dict]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.error("Payload no es JSON válido en tema '%s': %s — payload crudo: %r", topic, exc, payload_bytes)
        return None
    if not isinstance(payload, dict):
        log.error("El payload de '%s' debe ser un objeto JSON; llegó: %r", topic, payload)
        return None
    return payload


def _registrar_estado_soc(topic: str, payload_bytes: bytes, retenido: bool) -> None:
    """Recalibración guiada del SoC: solo queda en el log (sin tabla; la API la conoce por su
    propia suscripción, mqtt_publisher.py + servicios/estado_soc.py)."""
    datos = estado_soc.interpretar(payload_bytes)
    if datos is None:
        log.warning("Estado de recalibración del SoC no válido en '%s': %r", topic, payload_bytes[:200])
        return
    log.info(
        "Recalibración del SoC (%s)%s: %s | %s/%s min en reposo | motivo: %s | SoC actual %s, propuesto %s, %s V",
        topic, " [retenido]" if retenido else "", datos["estado"], datos["minutos_ok"], datos["requeridos"],
        datos["motivo"] or "-", datos["soc_actual"], datos["soc_propuesto"], datos["voltaje"],
    )


def _procesar_mensaje(topic: str, payload_bytes: bytes, retenido: bool = False) -> None:
    """`retenido`: la marca retain de MQTT (solo en lo que el broker entrega al suscribirse)."""
    partes = topic.split("/")
    if estado_soc.domo_del_tema(topic) is not None:
        _registrar_estado_soc(topic, payload_bytes, retenido)
        return
    if len(partes) == 5 and partes[0] == "domo" and partes[2] == "estado" and partes[3] in ("bombillo", "toma"):
        try:
            domo_id = int(partes[1])
        except ValueError:
            log.error("domo_id no numérico en el tema '%s': %r", topic, partes[1])
            return
        payload = _payload_json(topic, payload_bytes)
        if payload is None:
            return
        procesar = _procesar_estado_bombillo if partes[3] == "bombillo" else _procesar_estado_toma
        procesar(domo_id, partes[4], payload, retenido)
        return
    if len(partes) != 4 or partes[0] != "domo" or partes[2] != "telemetria":
        log.error("Tema con formato inesperado, se descarta: '%s' (se esperaba domo/{domo_id}/telemetria/{tipo})", topic)
        return

    domo_id_str, tipo = partes[1], partes[3]

    if tipo not in MAPEO_TIPOS:
        log.error("Tipo de telemetría desconocido '%s' en tema '%s' — tipos válidos: %s", tipo, topic, sorted(MAPEO_TIPOS))
        return

    try:
        domo_id = int(domo_id_str)
    except ValueError:
        log.error("domo_id no numérico en el tema '%s': %r", topic, domo_id_str)
        return

    payload = _payload_json(topic, payload_bytes)
    if payload is None:
        return

    campos, aceptados = MAPEO_TIPOS[tipo]
    insertada = _insertar_lectura(domo_id, tipo, campos, aceptados, payload)

    if insertada and tipo == "bateria":
        _evaluar_r13_automatico(domo_id)


def on_connect(client: mqtt.Client, userdata, connect_flags, reason_code, properties) -> None:
    if reason_code == 0:
        log.info(
            "Conectado a Mosquitto en %s:%s. Suscribiendo a '%s', '%s', '%s' y '%s'...",
            MQTT_BROKER_HOST, MQTT_BROKER_PORT, TOPIC_SUSCRIPCION, TOPIC_ESTADO_BOMBILLO, TOPIC_ESTADO_TOMA, TOPIC_ESTADO_SOC,
        )
        # Re-suscribir en cada on_connect (no solo la primera vez): tras
        # una reconexión, una sesión nueva del lado del broker no
        # conserva suscripciones anteriores.
        client.subscribe([(TOPIC_SUSCRIPCION, 1), (TOPIC_ESTADO_BOMBILLO, 1), (TOPIC_ESTADO_TOMA, 1), (TOPIC_ESTADO_SOC, 1)])
    else:
        log.error("Falló la conexión a Mosquitto: %s", reason_code)


def on_disconnect(client: mqtt.Client, userdata, disconnect_flags, reason_code, properties) -> None:
    if reason_code == 0:
        log.info("Desconectado de Mosquitto (solicitado).")
    else:
        log.warning("Conexión con Mosquitto perdida (%s) — reintentando automáticamente...", reason_code)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    # Solo encolar: guardar en la base de datos lo hace el hilo trabajador.
    # Aquí no se toca la base para no frenar la recepción de MQTT.
    _cola.put((msg.topic, bytes(msg.payload), bool(msg.retain), time.perf_counter()))


def _trabajador() -> None:
    """Saca mensajes de la cola en orden y los procesa uno a uno. Ningún
    error de un mensaje detiene el hilo."""
    while True:
        item = _cola.get()
        if item is None:
            break
        topic, payload, retenido, llegada = item
        inicio = time.perf_counter()
        try:
            _procesar_mensaje(topic, payload, retenido)
        except Exception:
            log.exception("Error inesperado procesando '%s' — el listener sigue.", topic)
        fin = time.perf_counter()
        log.info(
            "Procesado '%s' en %.2f s (esperó %.2f s en cola; en espera: %d).",
            topic, fin - inicio, inicio - llegada, _cola.qsize(),
        )


def _iniciar_clima() -> None:
    """Arranca el hilo del clima (primera consulta inmediata). Si no arranca, el
    listener sigue sin clima."""
    if not CLIMA_AUTO:
        log.info("Clima de referencia DESACTIVADO (CLIMA_AUTO=false).")
        return
    try:
        clima.iniciar_hilo(database.SessionLocal, CLIMA_INTERVALO_S)
        log.info("Clima de referencia (Open-Meteo) ACTIVADO: una consulta por domo cada %g s.", CLIMA_INTERVALO_S)
    except Exception:
        log.warning("No se pudo iniciar el hilo del clima; el listener sigue sin él.", exc_info=True)


def main() -> None:
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Reconexión automática con backoff exponencial entre 1 y 30 segundos
    # (paho-mqtt reintenta solo mientras loop_forever() esté corriendo).
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    log.info("Iniciando mqtt_listener — conectando a %s:%s...", MQTT_BROKER_HOST, MQTT_BROKER_PORT)
    if R13_AUTO:
        log.info("Evaluación automática de R-13 ACTIVADA (máximo una por domo cada %g s).", R13_AUTO_INTERVALO_S)
    else:
        log.info("Evaluación automática de R-13 DESACTIVADA (R13_AUTO=false).")
    trabajador = threading.Thread(target=_trabajador, name="guardado-lecturas", daemon=True)
    trabajador.start()
    _iniciar_clima()
    client.connect_async(MQTT_BROKER_HOST, MQTT_BROKER_PORT)

    try:
        # retry_first_connection=True: si Mosquitto todavía no está listo
        # cuando arranca este script, sigue reintentando en vez de morir.
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        log.info("Interrumpido por el usuario (Ctrl+C) — cerrando conexión...")
        client.disconnect()
        # Se deja terminar lo que ya estaba en cola (hasta 30 s) antes de salir.
        pendientes = _cola.qsize()
        if pendientes:
            log.info("Guardando %d mensaje(s) pendientes antes de salir...", pendientes)
        _cola.put(None)
        trabajador.join(timeout=30)
        if trabajador.is_alive():
            log.warning("Se cierra con %d mensaje(s) sin guardar.", _cola.qsize())


if __name__ == "__main__":
    main()
