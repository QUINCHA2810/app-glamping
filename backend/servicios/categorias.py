"""
Categorías de dispositivo y sus presets para el algoritmo de supervivencia
(R-13). Este es el único lugar con la fórmula: el endpoint
GET /domos/{id}/categorias la expone para que el frontend no la repita.

Modelo v2: los umbrales y la prioridad de una toma NO se guardan. Se calculan
siempre con su categoría y con el tipo de batería y el perfil del domo
(`domos.bateria_tipo`, `domos.perfil`). Cambiar el perfil o la batería cambia en
el acto cuándo R-13 corta cada toma, sin tocar los dispositivos.

Con la reserva de cada tipo (lifepo4 = 20 %, agm = 50 %), el apagado es:

    categoría      conservador   equilibrado   maximo_uso    prioridad
    esencial       (R-13 nunca la corta)                     40
    importante     reserva       reserva       reserva       30
    confort        reserva + 15  reserva + 10  reserva + 5   20
    prescindible   reserva + 30  reserva + 20  reserva + 10  10

y la reactivación, siempre el apagado + 10. Número de prioridad más bajo = se
corta primero.

Ya no hay umbrales personalizados por toma ni umbrales globales del domo
(decisión 5 de docs/modelo_v2/MAPEO.md). Donde la API devolvía los "umbrales
globales" (configuración, respuesta de /evaluar) ahora va el preset confort, que
era su valor por defecto.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from models import Domo

ESENCIAL = "esencial"
IMPORTANTE = "importante"
CONFORT = "confort"
PRESCINDIBLE = "prescindible"

# Orden de mayor a menor protección: el que devuelve GET /domos/{id}/categorias.
CATEGORIAS = (ESENCIAL, IMPORTANTE, CONFORT, PRESCINDIBLE)

PRIORIDAD_POR_CATEGORIA = {ESENCIAL: 40, IMPORTANTE: 30, CONFORT: 20, PRESCINDIBLE: 10}

CONSERVADOR = "conservador"
EQUILIBRADO = "equilibrado"
MAXIMO_USO = "maximo_uso"
PERFILES = (CONSERVADOR, EQUILIBRADO, MAXIMO_USO)
PERFIL_POR_DEFECTO = CONSERVADOR

# Cuánto sobre la reserva se apaga cada categoría, según el perfil. Esencial no
# aparece: R-13 nunca la corta, así que no tiene umbrales.
_SOBRE_LA_RESERVA = {
    CONSERVADOR: {IMPORTANTE: 0.0, CONFORT: 15.0, PRESCINDIBLE: 30.0},
    EQUILIBRADO: {IMPORTANTE: 0.0, CONFORT: 10.0, PRESCINDIBLE: 20.0},
    MAXIMO_USO: {IMPORTANTE: 0.0, CONFORT: 5.0, PRESCINDIBLE: 10.0},
}
BANDA_DE_REACTIVACION = 10.0

RESERVA_SOC_POR_BATERIA = {"lifepo4": 20.0, "agm": 50.0}
TIPO_BATERIA_POR_DEFECTO = "lifepo4"


@dataclass(frozen=True)
class Preset:
    categoria: str
    umbral_apagado_soc: Optional[float]
    umbral_reactivacion_soc: Optional[float]
    prioridad: int


def preset_de(categoria: str, tipo_bateria: str, perfil: str) -> Preset:
    prioridad = PRIORIDAD_POR_CATEGORIA[categoria]
    if categoria == ESENCIAL:
        return Preset(categoria, None, None, prioridad)
    apagado = RESERVA_SOC_POR_BATERIA[tipo_bateria] + _SOBRE_LA_RESERVA[perfil][categoria]
    return Preset(categoria, apagado, apagado + BANDA_DE_REACTIVACION, prioridad)


def presets_de(tipo_bateria: str, perfil: str) -> list[Preset]:
    return [preset_de(categoria, tipo_bateria, perfil) for categoria in CATEGORIAS]


def tipo_bateria_de(domo: Optional[Domo]) -> str:
    """Tipo de batería del domo, o el de por defecto si no hay domo o no es válido."""
    tipo = getattr(domo, "bateria_tipo", None)
    return tipo if tipo in RESERVA_SOC_POR_BATERIA else TIPO_BATERIA_POR_DEFECTO


def perfil_de(domo: Optional[Domo]) -> str:
    perfil = getattr(domo, "perfil", None)
    return perfil if perfil in PERFILES else PERFIL_POR_DEFECTO


def reserva_de(domo: Optional[Domo]) -> float:
    """Reserva de la batería: se deriva del tipo, no se guarda (decisión 1)."""
    return RESERVA_SOC_POR_BATERIA[tipo_bateria_de(domo)]


def ajustes_del_domo(db: Session, domo_id: int) -> tuple[str, str]:
    """(tipo de batería, perfil): lo que define los presets de un domo."""
    domo = db.get(Domo, domo_id)
    return tipo_bateria_de(domo), perfil_de(domo)


def tipo_bateria_del_domo(db: Session, domo_id: int) -> str:
    return tipo_bateria_de(db.get(Domo, domo_id))


def umbrales_de(categoria: str, tipo_bateria: str, perfil: str) -> tuple[Optional[float], Optional[float]]:
    """(apagado, reactivación) de una toma de esa categoría. (None, None) si es esencial."""
    preset = preset_de(categoria, tipo_bateria, perfil)
    return preset.umbral_apagado_soc, preset.umbral_reactivacion_soc


def umbrales_globales_de(tipo_bateria: str, perfil: str) -> tuple[float, float]:
    """Lo que la API sigue llamando umbrales globales del domo: el preset confort."""
    preset = preset_de(CONFORT, tipo_bateria, perfil)
    return preset.umbral_apagado_soc, preset.umbral_reactivacion_soc
