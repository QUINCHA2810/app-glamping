"""
Iluminación del bombillo: modos, escenas, presets, valores válidos de `color` y
`temperatura_color`, y las conversiones hacia y desde el contrato MQTT del firmware.
Es el ÚNICO lugar con estas listas: schemas.py valida con ellas, GET /iluminacion/opciones
las entrega al frontend, routers/dispositivos.py arma las órdenes y mqtt_listener.py
traduce el estado que publica el ESP32.

Contrato con el firmware (firmware/domo_nodo/domo_nodo.ino, leerCamposDeLuz y
publicarEstado), además de accion y brillo:
- "modo": "blanco" | "color" | "escena"
- "temperatura": 0-100 (0 = más cálido), solo en modo blanco
- "color": {"h": 0-360, "s": 0-100}, solo en modo color; el brillo es el V del color
- "escena": un nombre de ESCENAS, que coincide con ESCENAS_BOMBILLO del firmware
  (firmware/domo_nodo/dispositivos_config.h; lo comprueba tests/test_iluminacion.py)
- Una orden no mezcla campos de modos distintos.

En la base:
- `modo_luz`: blanco | color | escena (NULL = sin modo: el bombillo solo recibe
  encendido y brillo, como antes).
- `temperatura_color`: calida | neutra | fria, que el firmware recibe como 0 / 50 / 100.
  El estado del bombillo se guarda con la más cercana.
- `color`: "#rrggbb" o uno de los nombres de COLORES (los valores de antes siguen
  valiendo); cada nombre tiene un tono fijo (TONOS_COLOR). Hacia el firmware solo van
  H y S: el brillo es aparte.
- `escena`: un nombre de ESCENAS.

Los valores guardados antes de esta lista (p. ej. "string", que entró por /docs) se
siguen leyendo sin error: DispositivoOut no los valida, el frontend los muestra como
"Sin definir", y una orden no lleva el campo que no se entiende.
"""

import colorsys
import re
from typing import Optional

MODOS_LUZ: tuple[str, ...] = ("blanco", "color", "escena")

# (valor guardado, nombre para mostrar). Los valores, en el mismo orden que
# ESCENAS_BOMBILLO del firmware.
ESCENAS: tuple[tuple[str, str], ...] = (
    ("fiesta", "Fiesta"),
    ("arcoiris", "Arcoíris"),
    ("romantico", "Romántico"),
    ("disco", "Disco"),
    ("estrobo", "Estrobo"),
    ("vela", "Vela"),
    ("atardecer", "Atardecer"),
    ("oceano", "Océano"),
)

COLORES: tuple[tuple[str, str], ...] = (
    ("blanco", "Blanco"),
    ("amarillo", "Amarillo"),
    ("naranja", "Naranja"),
    ("rojo", "Rojo"),
    ("rosa", "Rosa"),
    ("morado", "Morado"),
    ("azul", "Azul"),
    ("verde", "Verde"),
)
# Tono fijo de cada nombre: (h 0-360, s 0-100).
TONOS_COLOR: dict[str, tuple[int, int]] = {
    "blanco": (0, 0),
    "amarillo": (60, 100),
    "naranja": (30, 100),
    "rojo": (0, 100),
    "rosa": (330, 60),
    "morado": (280, 100),
    "azul": (240, 100),
    "verde": (120, 100),
}

TEMPERATURAS_COLOR: tuple[tuple[str, str], ...] = (
    ("calida", "Cálida"),
    ("neutra", "Neutra"),
    ("fria", "Fría"),
)
# Lo que recibe el firmware: 0 = más cálido, 100 = más frío.
VALOR_TEMPERATURA: dict[str, int] = {"calida": 0, "neutra": 50, "fria": 100}

# Presets de luz blanca: un preset se aplica con un solo PATCH (modo_luz, temperatura_color
# y, si lo fija, brillo). brillo None = conserva el brillo actual.
PRESETS: tuple[dict, ...] = (
    {"valor": "calido", "etiqueta": "Cálido", "temperatura_color": "calida", "brillo": None},
    {"valor": "neutro", "etiqueta": "Neutro", "temperatura_color": "neutra", "brillo": None},
    {"valor": "frio", "etiqueta": "Frío", "temperatura_color": "fria", "brillo": None},
    {"valor": "lectura", "etiqueta": "Lectura", "temperatura_color": "fria", "brillo": 100},
    {"valor": "relajacion", "etiqueta": "Relajación", "temperatura_color": "calida", "brillo": 30},
    {"valor": "noche", "etiqueta": "Noche", "temperatura_color": "calida", "brillo": 10},
)

VALORES_COLOR = tuple(valor for valor, _ in COLORES)
VALORES_TEMPERATURA_COLOR = tuple(valor for valor, _ in TEMPERATURAS_COLOR)
VALORES_ESCENA = tuple(valor for valor, _ in ESCENAS)

SE_ENVIAN_AL_BOMBILLO = True

_HEX = re.compile(r"^#[0-9a-f]{6}$")


def normalizar(valor: str, validos: tuple[str, ...]) -> str:
    """El valor sin espacios y en minúsculas, si está en la lista; si no, ValueError
    con las opciones (el mensaje del 422)."""
    limpio = valor.strip().lower()
    if limpio not in validos:
        raise ValueError(f"Debe ser uno de: {', '.join(validos)}.")
    return limpio


def normalizar_color(valor: str) -> str:
    """Un nombre de COLORES o "#rrggbb", sin espacios y en minúsculas; si no, ValueError."""
    limpio = valor.strip().lower()
    if limpio in VALORES_COLOR or _HEX.match(limpio):
        return limpio
    raise ValueError(f"Debe ser uno de: {', '.join(VALORES_COLOR)}, o un color #rrggbb.")


def hex_a_hs(color: str) -> tuple[int, int]:
    """"#rrggbb" -> (h 0-360, s 0-100). El brillo (V) se ignora: va aparte."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return round(h * 360) % 360, round(s * 100)


def hs_a_hex(h: int, s: int) -> str:
    """(h 0-360, s 0-100) -> "#rrggbb" con el brillo al máximo."""
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, max(0, min(100, s)) / 100, 1.0)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def color_a_hs(color: Optional[str]) -> Optional[tuple[int, int]]:
    """H y S de un color guardado (nombre o "#rrggbb"), o None si no se entiende."""
    if color is None:
        return None
    limpio = color.strip().lower()
    if limpio in TONOS_COLOR:
        return TONOS_COLOR[limpio]
    if _HEX.match(limpio):
        return hex_a_hs(limpio)
    return None


def temperatura_mas_cercana(valor: int) -> str:
    """El valor de TEMPERATURAS_COLOR más cercano a una temperatura 0-100 del firmware."""
    return min(VALOR_TEMPERATURA, key=lambda nombre: abs(VALOR_TEMPERATURA[nombre] - valor))


def _entero_en(valor: object, minimo: int, maximo: int) -> Optional[int]:
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        return None
    return valor


def campos_del_estado(estado: dict) -> dict:
    """Las columnas de `dispositivos` que se actualizan con el estado que publica el ESP32
    (domo/{id}/estado/bombillo/{id_fisico}). Solo lo que viene y se entiende: un firmware
    viejo sin modo, temperatura ni color no borra nada. La escena no viene en el estado:
    se conserva la guardada."""
    campos: dict = {}
    if estado.get("modo") in MODOS_LUZ:
        campos["modo_luz"] = estado["modo"]
    brillo = _entero_en(estado.get("brillo"), 0, 100)
    if brillo is not None:
        campos["brillo"] = brillo
    temperatura = _entero_en(estado.get("temperatura"), 0, 100)
    if temperatura is not None:
        campos["temperatura_color"] = temperatura_mas_cercana(temperatura)
    color = estado.get("color")
    if isinstance(color, dict):
        h, s = _entero_en(color.get("h"), 0, 360), _entero_en(color.get("s"), 0, 100)
        if h is not None and s is not None:
            campos["color"] = hs_a_hex(h, s)
    return campos


def orden_de_luz(modo_luz: Optional[str], brillo: Optional[int], color: Optional[str],
                 temperatura_color: Optional[str], escena: Optional[str]) -> dict:
    """Los campos de luz de una orden MQTT para el estado guardado de un bombillo, en su
    modo. Sin modo: solo el brillo (el comportamiento de antes). Un valor guardado que no
    se entiende (p. ej. un color viejo "string") no se manda."""
    if modo_luz == "blanco":
        orden: dict = {"modo": "blanco"}
        if brillo is not None:
            orden["brillo"] = brillo
        if temperatura_color in VALOR_TEMPERATURA:
            orden["temperatura"] = VALOR_TEMPERATURA[temperatura_color]
        return orden
    if modo_luz == "color":
        orden = {"modo": "color"}
        if brillo is not None:
            orden["brillo"] = brillo
        hs = color_a_hs(color)
        if hs is not None:
            orden["color"] = {"h": hs[0], "s": hs[1]}
        return orden
    if modo_luz == "escena":
        orden = {"modo": "escena"}
        if escena in VALORES_ESCENA:
            orden["escena"] = escena
        return orden
    return {"brillo": brillo} if brillo is not None else {}
