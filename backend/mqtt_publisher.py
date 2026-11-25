"""
Publicador MQTT del backend — manda órdenes hacia las tomas y los bombillos
FÍSICOS reales del domo (los dispositivos Tuya que controla el ESP32, ver
firmware/domo_nodo/).

Es la contraparte de salida de `mqtt_listener.py`: ese escucha telemetría
que entra, este publica comandos que salen. A diferencia del listener,
NO corre como proceso aparte — es un módulo que importan los routers y
que vive dentro del mismo proceso de uvicorn.

Tema y payload (contrato ya probado contra el ESP32, no cambiarlo sin
tocar también el firmware):

    domo/{domo_id}/comando/toma      →  {"accion": "on"|"off",
                                         "id_fisico": "<id del dispositivo>",
                                         "origen": "r13"|"manual"}

    domo/{domo_id}/comando/bombillo  →  {"id_fisico": "<id del dispositivo>",
                                         "origen": "manual",
                                         "accion": "on"|"off",               (opcional)
                                         "brillo": 0-100,                    (opcional)
                                         "modo": "blanco"|"color"|"escena",  (opcional)
                                         "temperatura": 0-100,               (solo blanco)
                                         "color": {"h": 0-360, "s": 0-100},  (solo color)
                                         "escena": "<nombre>"}               (solo escena)
                                        Al menos un campo además de id_fisico y origen,
                                        sin mezclar campos de modos distintos, y sin modo
                                        si la acción es "off". Solo lo publica un PATCH de
                                        un usuario: R-13 nunca toca bombillos. Las escenas
                                        válidas, en servicios/iluminacion.py.

    domo/{domo_id}/comando/soc       →  {"accion": "iniciar"|"confirmar"|"cancelar",
                                         "origen": "app"}
                                        Recalibración guiada del SoC: solo la pide el
                                        dueño (routers/soc_recalibracion.py). NUNCA lleva
                                        un valor de SoC: lo mide la ESP32 en reposo.

Recepción (este mismo cliente): se suscribe a `domo/+/estado/soc` (RETENIDO, lo
publica el ESP32) y entrega cada mensaje a servicios/estado_soc.py, que guarda el
último estado en memoria. Hay UN cliente por proceso (uvicorn --reload: el proceso
que sirve la API), con su hilo de red (`loop_start`), que también recibe; la
suscripción se repite en cada `_on_connect`, así que sobrevive a una reconexión, y el
broker entrega el retenido apenas se suscribe.

El ESP32 responde en `domo/{domo_id}/estado/toma/{id_fisico}` y
`domo/{domo_id}/estado/bombillo/{id_fisico}`; esa confirmación todavía no
se consume en el backend (la conciliación entre el estado guardado y el
estado real del hardware queda pendiente).

Decisiones de diseño:

- retain=False, SIEMPRE. Un comando retenido se lo volvería a entregar
  el broker al ESP32 en cada reconexión, y el ESP32 reejecutaría una
  orden vieja — por ejemplo, apagaría la toma otra vez horas después de
  que alguien la encendió a mano. Los mensajes de ESTADO sí son
  retenidos (los publica el firmware), los de COMANDO no.
- qos=1: el comando se entrega al menos una vez. Reejecutar un "on"
  sobre una toma ya encendida es inofensivo (es idempotente), perderlo
  no lo es.
- Esta función NUNCA lanza excepción por fallos de red: loguea y
  devuelve False. Un broker caído no debe romper ningún endpoint de la
  API — la base de datos es la fuente de verdad del estado deseado, y
  MQTT es el efecto secundario que lo propaga al hardware. Lo único que
  sí lanza (ValueError) es una `accion` inválida, porque eso es un error
  de programación del backend, no una condición del entorno.
- Un único cliente global con inicialización perezosa y `loop_start()`
  (un hilo en segundo plano que mantiene la conexión viva y reconecta
  solo). Se crea en la primera publicación, no al importar el módulo:
  así levantar la API nunca depende de que Mosquitto esté arriba.

Limitación conocida: si el broker está caído, paho encola el mensaje
qos=1 y lo entrega cuando reconecta, aunque `publicar_comando_toma` ya
haya devuelto False. Es decir, una orden puede llegar con retraso al
ESP32. Es mucho menos grave que `retain=True` (la orden se entrega una
vez, no en cada reconexión futura), y desaparecerá cuando se implemente
la conciliación contra `domo/{domo_id}/estado/toma`.

Configuración: MQTT_BROKER_HOST / MQTT_BROKER_PORT en backend/.env
(las mismas variables que usa mqtt_listener.py).
"""

import json
import logging
import os
import threading
from typing import Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from servicios import estado_soc
from servicios.iluminacion import MODOS_LUZ, VALORES_ESCENA

load_dotenv()

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

ACCIONES_VALIDAS = ("on", "off")
ACCIONES_SOC = ("iniciar", "confirmar", "cancelar")

log = logging.getLogger("mqtt_publisher")

# Cliente único reutilizado entre llamadas (ver docstring del módulo). El
# lock protege la creación: con uvicorn, dos requests concurrentes pueden
# entrar a _obtener_cliente() a la vez y crearían dos clientes, cada uno
# con su propio hilo de red.
_cliente: Optional[mqtt.Client] = None
_lock_cliente = threading.Lock()


def _tema_comando_toma(domo_id: int) -> str:
    return f"domo/{domo_id}/comando/toma"


def _tema_comando_bombillo(domo_id: int) -> str:
    return f"domo/{domo_id}/comando/bombillo"


def _tema_comando_soc(domo_id: int) -> str:
    return f"domo/{domo_id}/comando/soc"


# Se marca con el SUBACK de domo/+/estado/soc: desde ahí el broker ya entregó el retenido.
_suscrito_estado_soc = threading.Event()


def _on_connect(client: mqtt.Client, userdata, connect_flags, reason_code, properties) -> None:
    if reason_code == 0:
        log.info("Publicador conectado a Mosquitto en %s:%s.", MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        # En CADA conexión (no solo la primera): tras reconectar, una sesión nueva del
        # broker no conserva las suscripciones anteriores.
        client.subscribe(estado_soc.TEMA_ESTADO_SOC, qos=1)
    else:
        log.error("El publicador no pudo conectarse a Mosquitto: %s", reason_code)


def _on_subscribe(client: mqtt.Client, userdata, mid, reason_codes, properties) -> None:
    _suscrito_estado_soc.set()


def _on_estado_soc(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    # Corre en el hilo de red de paho: solo guarda en memoria (no toca la base).
    try:
        estado_soc.registrar(msg.topic, bytes(msg.payload))
    except Exception:
        log.exception("Error guardando el estado de la recalibración del SoC de '%s'.", msg.topic)


def _on_disconnect(client: mqtt.Client, userdata, disconnect_flags, reason_code, properties) -> None:
    if reason_code == 0:
        log.info("Publicador desconectado de Mosquitto (solicitado).")
    else:
        log.warning("El publicador perdió la conexión con Mosquitto (%s) — reintentando automáticamente...", reason_code)


def _obtener_cliente() -> mqtt.Client:
    """Cliente MQTT global, creándolo y conectándolo la primera vez.

    Puede lanzar (OSError si el broker no está disponible, por ejemplo):
    quien llama es responsable de atraparlo — ver _publicar y
    escuchar_estado_soc. Si la conexión inicial falla, el cliente
    NO queda guardado, así que la siguiente publicación vuelve a
    intentarlo desde cero (si quedara guardado a medio conectar, todas
    las publicaciones siguientes fallarían aunque Mosquitto ya hubiera
    vuelto).
    """
    global _cliente
    with _lock_cliente:
        if _cliente is not None:
            return _cliente

        # Misma versión de API de callbacks que mqtt_listener.py
        # (VERSION2) — las firmas de los callbacks de arriba dependen de
        # eso. Sin client_id fijo a propósito: paho genera uno aleatorio,
        # y así dos procesos de uvicorn no se expulsan mutuamente del
        # broker por usar el mismo identificador.
        cliente = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        cliente.on_connect = _on_connect
        cliente.on_disconnect = _on_disconnect
        cliente.on_subscribe = _on_subscribe
        cliente.message_callback_add(estado_soc.TEMA_ESTADO_SOC, _on_estado_soc)
        cliente.reconnect_delay_set(min_delay=1, max_delay=30)

        log.info("Iniciando publicador MQTT — conectando a %s:%s...", MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        cliente.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
        # loop_start() y no loop_forever(): esto corre dentro de uvicorn,
        # el bucle de red tiene que vivir en su propio hilo y no bloquear
        # al que atiende el request.
        cliente.loop_start()

        _cliente = cliente
        return _cliente


def publicar_comando_toma(domo_id: int, accion: str, id_fisico: str, origen: str) -> bool:
    """Publica una orden de encendido/apagado para la toma física del domo.

    - `domo_id`: domo al que pertenece la toma (va en el tema).
    - `accion`: "on" u "off". Cualquier otro valor es un ValueError.
    - `id_fisico`: valor de `dispositivos.id_fisico` de esa toma (viaja
      en el payload). Las filas con `id_fisico` NULL son dispositivos
      simulados y NO deben llegar hasta acá — quien llama es el que
      filtra (ver routers/control_energetico.py y routers/dispositivos.py).
    - `origen`: quién pidió el cambio, para poder rastrearlo desde el
      firmware o desde `mosquitto_sub`. "r13" = algoritmo de
      supervivencia, "manual" = un usuario desde la API.

    Devuelve True si el comando se entregó al broker, False si no se
    pudo (broker caído, red, cualquier otro fallo). Nunca lanza por un
    fallo de red: el estado ya quedó guardado en la base de datos y el
    endpoint que llamó no debe fallar por esto.
    """
    if accion not in ACCIONES_VALIDAS:
        raise ValueError(f"accion debe ser 'on' u 'off'; llegó: {accion!r}")
    return _publicar(_tema_comando_toma(domo_id), {"accion": accion, "id_fisico": id_fisico, "origen": origen})


def _es_entero_en(valor, minimo: int, maximo: int) -> bool:
    return not isinstance(valor, bool) and isinstance(valor, int) and minimo <= valor <= maximo


def publicar_comando_bombillo(
    domo_id: int,
    id_fisico: str,
    origen: str,
    accion: Optional[str] = None,
    brillo: Optional[int] = None,
    modo: Optional[str] = None,
    temperatura: Optional[int] = None,
    color: Optional[dict] = None,
    escena: Optional[str] = None,
) -> bool:
    """Publica una orden para el bombillo físico (contrato en el docstring del módulo).

    - `accion`: "on", "off" o None (sin cambio de encendido).
    - `brillo`: entero 0-100 (la escala de la app) o None. El ESP32 lo convierte a la
      escala del bombillo; en modo color es el V del color.
    - `modo`: "blanco", "color", "escena" o None (sin modo: solo accion y brillo).
    - `temperatura` (0-100, 0 = más cálido), `color` ({"h": 0-360, "s": 0-100}) y
      `escena` (un nombre de servicios/iluminacion.ESCENAS): cada uno solo con su modo
      (o sin modo: el firmware lo deduce).
    Sin ningún campo, con valores fuera de rango, mezclando modos o con un modo y
    accion="off" es un ValueError (un error de programación). Igual que
    publicar_comando_toma: retain=False, qos=1, y False en vez de excepción ante un
    fallo de red.
    """
    if accion is not None and accion not in ACCIONES_VALIDAS:
        raise ValueError(f"accion debe ser 'on', 'off' o None; llegó: {accion!r}")
    if brillo is not None and not _es_entero_en(brillo, 0, 100):
        raise ValueError(f"brillo debe ser un entero de 0 a 100 o None; llegó: {brillo!r}")
    if modo is not None and modo not in MODOS_LUZ:
        raise ValueError(f"modo debe ser uno de {MODOS_LUZ} o None; llegó: {modo!r}")
    if temperatura is not None and not _es_entero_en(temperatura, 0, 100):
        raise ValueError(f"temperatura debe ser un entero de 0 a 100 o None; llegó: {temperatura!r}")
    if color is not None and not (
        isinstance(color, dict) and set(color) == {"h", "s"}
        and _es_entero_en(color["h"], 0, 360) and _es_entero_en(color["s"], 0, 100)
    ):
        raise ValueError(f"color debe ser {{'h': 0-360, 's': 0-100}} o None; llegó: {color!r}")
    if escena is not None and escena not in VALORES_ESCENA:
        raise ValueError(f"escena debe ser una de {VALORES_ESCENA} o None; llegó: {escena!r}")

    del_modo = {"blanco": temperatura, "color": color, "escena": escena}
    con_valor = [m for m, valor in del_modo.items() if valor is not None]
    if len(con_valor) > 1:
        raise ValueError(f"La orden mezcla campos de modos distintos: {con_valor}.")
    if modo is not None and con_valor and con_valor[0] != modo:
        raise ValueError(f"El campo de '{con_valor[0]}' no corresponde al modo {modo!r}.")
    if modo == "escena" and brillo is not None:
        raise ValueError("En modo escena no se manda brillo.")
    if accion == "off" and (modo is not None or con_valor):
        raise ValueError("Una orden de apagado no lleva modo: el modo enciende el bombillo.")
    if accion is None and brillo is None and modo is None and not con_valor:
        raise ValueError("La orden del bombillo necesita accion, brillo o un modo de luz.")

    cuerpo: dict = {"id_fisico": id_fisico, "origen": origen}
    for campo, valor in (("accion", accion), ("brillo", brillo), ("modo", modo),
                         ("temperatura", temperatura), ("color", color), ("escena", escena)):
        if valor is not None:
            cuerpo[campo] = valor
    return _publicar(_tema_comando_bombillo(domo_id), cuerpo)


def publicar_comando_soc(domo_id: int, accion: str) -> bool:
    """Orden de la recalibración guiada del SoC: "iniciar", "confirmar" o "cancelar"
    (otro valor: ValueError). Solo la acción: nunca un valor de SoC. Como las demás
    órdenes, retain=False y qos=1, y False en vez de excepción ante un fallo de red."""
    if accion not in ACCIONES_SOC:
        raise ValueError(f"accion debe ser una de {ACCIONES_SOC}; llegó: {accion!r}")
    return _publicar(_tema_comando_soc(domo_id), {"accion": accion, "origen": "app"})


def escuchar_estado_soc(espera_s: float = 2.0) -> bool:
    """Se asegura de que el cliente exista (y por lo tanto esté suscrito a
    domo/+/estado/soc). La primera vez espera hasta `espera_s` el SUBACK, para que el
    estado retenido ya esté en memoria. False si el broker no está disponible."""
    try:
        _obtener_cliente()
    except Exception:
        log.warning("Sin conexión con Mosquitto: no se conoce el estado de la recalibración del SoC.")
        return False
    _suscrito_estado_soc.wait(espera_s)
    return True


def _publicar(tema: str, cuerpo: dict) -> bool:
    payload = json.dumps(cuerpo)
    try:
        cliente = _obtener_cliente()
        # retain=False: NUNCA retenido, ver docstring del módulo.
        info = cliente.publish(tema, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.error(
                "No se pudo publicar el comando en '%s' (payload %s): código %s de paho-mqtt.",
                tema, payload, info.rc,
            )
            return False
        log.info("Comando publicado en '%s': %s", tema, payload)
        return True
    except Exception:
        log.exception(
            "Error publicando el comando en '%s' (payload %s) — el estado ya quedó guardado en la base de datos.",
            tema, payload,
        )
        return False
