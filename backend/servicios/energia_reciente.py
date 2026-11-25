"""
Autonomía y flujo de energía PROMEDIADOS, para el Panel (GET /domos/{id}/energia-reciente).

Con valores instantáneos la autonomía saltaba (un pico de 10 s la hundía y al pasar se
disparaba) y el flujo mostraba incoherencias, porque la corriente FV del MPPT pulsa y la
potencia de la batería es estimada (firmware: P_bat = P_FV·η − V_bat·I_inv − propio).
Aquí se promedia en el backend, así todos los clientes ven lo mismo.

Ventanas ANCLADAS A LA ÚLTIMA MEDICIÓN con alguna potencia (no a "ahora"):
- autonomía: los últimos VENTANA_AUTONOMIA_S (15 min);
- flujo: los últimos VENTANA_FLUJO_S (60 s, ~6 bloques de 10 s), con las pérdidas del
  inversor (entrada DC − consumo AC, ver perdidas_inversor).
Si esa última medición tiene más de mediciones.VIGENCIA_S (120 s), no hay ni autonomía ni
flujo (`vigente: false`), igual que el resto del Panel.

Huecos: se promedian solo las filas que existen; no se rellena nada. Cada fila es un bloque
de 10 s, así que los minutos con datos son filas × 10 s / 60, y eso es lo que muestra la
tarjeta ("promedio de los últimos 6 min").

Autonomía, con la potencia NETA media de la batería (`potencia_bateria_w`, + carga, − descarga):
es la misma potencia con la que el firmware mueve el SoC, así que ya cuenta los paneles, las
pérdidas del inversor y el consumo propio del ESP32. Energía utilizable =
capacidad × (SoC − reserva) / 100.
- descarga media >= POTENCIA_CERO_W: horas = energía / descarga (más de TOPE_HORAS: "más de");
- carga media >= POTENCIA_CERO_W: "cargando", y aparte "sin sol" con la carga DC media;
- entre las dos: casi cero, "más de TOPE_HORAS".
"Sin sol" usa la carga DC real por fila = potencia_fv_w × EFICIENCIA_MPPT − potencia_bateria_w,
que es V_bat·I_inv + CONSUMO_PROPIO_W (la fórmula del firmware despejada). Una fila sin
potencia_fv_w cuenta la FV como 0, solo si tiene potencia_bateria_w.

Nada de esto toca R-13, que sigue decidiendo con el SoC actual.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean
from typing import Optional

from sqlalchemy.orm import Session

from models import Medicion
from servicios.mediciones import BLOQUE_S, VIGENCIA_S, a_utc

VENTANA_AUTONOMIA_S = 15 * 60
VENTANA_FLUJO_S = 60
# Con menos datos que esto en la ventana de 15 min no se estima la autonomía.
MINIMO_DATOS_S = 2 * 60
# Más horas que esto se muestran como "más de 72 h": con potencias tan bajas el número no
# significa nada.
TOPE_HORAS = 72.0
# Por debajo de esto (en valor absoluto) una potencia media se considera cero.
POTENCIA_CERO_W = 5.0
# Copia de EFICIENCIA_MPPT de firmware/domo_nodo/sensores_config.h (tests/test_energia_reciente.py
# las compara): la potencia FV publicada es la de ANTES del MPPT.
EFICIENCIA_MPPT = 0.95
# Con menos entrada al inversor que esto la eficiencia no se calcula (null): el error de las
# lecturas pesa más que la potencia.
ENTRADA_MINIMA_W = 20.0

# Se leen las filas de la ventana de autonomía más la vigencia: la última medición puede
# tener hasta VIGENCIA_S y su ventana empieza 15 min antes.
_LECTURA_S = VENTANA_AUTONOMIA_S + VIGENCIA_S + BLOQUE_S


@dataclass(frozen=True)
class Fila:
    t: datetime
    fv: Optional[float] = None
    consumo: Optional[float] = None
    bateria: Optional[float] = None
    soc: Optional[float] = None

    @property
    def tiene_potencia(self) -> bool:
        return self.fv is not None or self.consumo is not None or self.bateria is not None


def leer_filas(db: Session, domo_id: int, ahora: datetime) -> list[Fila]:
    """Las mediciones del domo de los últimos ~17 min, de la más antigua a la más reciente.
    Unas 100 filas como mucho; la UNIQUE (domo_id, fecha_hora) cubre el filtro."""
    filas = (
        db.query(
            Medicion.fecha_hora, Medicion.potencia_fv_w, Medicion.potencia_consumo_w,
            Medicion.potencia_bateria_w, Medicion.soc_pct,
        )
        .filter(Medicion.domo_id == domo_id, Medicion.fecha_hora >= a_utc(ahora) - timedelta(seconds=_LECTURA_S))
        .order_by(Medicion.fecha_hora)
        .all()
    )
    return [Fila(a_utc(f[0]), f[1], f[2], f[3], f[4]) for f in filas]


def _media(valores: list[float]) -> Optional[float]:
    return fmean(valores) if valores else None


def _duracion(energia_wh: float, potencia_w: Optional[float]) -> tuple[Optional[float], bool]:
    """(horas, supera_tope). Con potencia casi cero, o más de TOPE_HORAS: (None, True)."""
    if potencia_w is None:
        return None, False
    if potencia_w < POTENCIA_CERO_W:
        return None, True
    horas = energia_wh / potencia_w
    return (None, True) if horas > TOPE_HORAS else (horas, False)


def perdidas_inversor(generada: Optional[float], consumida: Optional[float], bateria: Optional[float]) -> dict:
    """Paneles y batería están en DC; las cargas, en AC (PZEM a la salida del inversor).

    - entrada_inversor_w: lo que sale de paneles + batería hacia las cargas = generada −
      bateria (descargando: PV + descarga; cargando: PV − carga). Sin generada o sin batería,
      None.
    - perdidas_w = max(0, entrada − consumida).
    - eficiencia_pct = consumida / entrada × 100, solo con entrada >= ENTRADA_MINIMA_W y sin
      pasar de 100 % (más de 100 % es una incoherencia de lecturas, que ya avisa el Panel).
    La potencia FV publicada es la de ANTES del MPPT y la de la batería es estimada, así que la
    "pérdida" incluye también el MPPT (~5 %) y el consumo propio (~3 W): es aproximada."""
    salida = {"entrada_inversor_w": None, "perdidas_w": None, "eficiencia_pct": None}
    if generada is None or bateria is None:
        return salida
    entrada = generada - bateria
    salida["entrada_inversor_w"] = entrada
    if consumida is None:
        return salida
    salida["perdidas_w"] = max(0.0, entrada - consumida)
    if entrada >= ENTRADA_MINIMA_W and consumida <= entrada:
        salida["eficiencia_pct"] = consumida / entrada * 100
    return salida


def flujo_promedio(filas: list[Fila], fin: datetime) -> dict:
    """Generación, consumo y batería medios de los VENTANA_FLUJO_S que terminan en `fin`.
    Cada uno con sus filas no nulas; sin ninguna, None ("sin dato"). Más las pérdidas del
    inversor con esos promedios (perdidas_inversor)."""
    ventana = [f for f in filas if fin - timedelta(seconds=VENTANA_FLUJO_S) < f.t <= fin]
    generada = _media([f.fv for f in ventana if f.fv is not None])
    consumida = _media([f.consumo for f in ventana if f.consumo is not None])
    bateria = _media([f.bateria for f in ventana if f.bateria is not None])
    return {
        "generada": generada,
        "consumida": consumida,
        "bateria": bateria,
        **perdidas_inversor(generada, consumida, bateria),
    }


def autonomia_promedio(
    filas: list[Fila], fin: datetime, soc: Optional[float], capacidad_wh: Optional[float], reserva: float
) -> dict:
    """La autonomía con la potencia media de los VENTANA_AUTONOMIA_S que terminan en `fin`.
    `tipo` dice qué mostrar: horas | mas_del_tope | cargando | reserva | sin_capacidad |
    pocos_datos | sin_datos."""
    salida = {
        "tipo": "sin_datos", "horas": None, "horas_sin_sol": None, "sin_sol_supera_tope": False,
        "potencia_media_w": None, "carga_dc_media_w": None, "minutos": 0.0,
        "ventana_min": VENTANA_AUTONOMIA_S / 60, "tope_horas": TOPE_HORAS,
    }
    ventana = [f for f in filas if fin - timedelta(seconds=VENTANA_AUTONOMIA_S) < f.t <= fin and f.bateria is not None]
    salida["minutos"] = len(ventana) * BLOQUE_S / 60
    if ventana:
        salida["potencia_media_w"] = fmean(f.bateria for f in ventana)
        salida["carga_dc_media_w"] = fmean((f.fv or 0.0) * EFICIENCIA_MPPT - f.bateria for f in ventana)

    if soc is None:
        return salida
    if capacidad_wh is None or capacidad_wh <= 0:
        return {**salida, "tipo": "sin_capacidad"}
    if soc <= reserva:
        return {**salida, "tipo": "reserva"}
    if len(ventana) * BLOQUE_S < MINIMO_DATOS_S:
        return {**salida, "tipo": "pocos_datos"}

    energia_wh = capacidad_wh * (soc - reserva) / 100
    horas_sin_sol, sin_sol_tope = _duracion(energia_wh, salida["carga_dc_media_w"])
    salida.update(horas_sin_sol=horas_sin_sol, sin_sol_supera_tope=sin_sol_tope)
    media = salida["potencia_media_w"]
    if media >= POTENCIA_CERO_W:
        return {**salida, "tipo": "cargando"}
    horas, supera_tope = _duracion(energia_wh, -media)
    return {**salida, "tipo": "mas_del_tope" if supera_tope else "horas", "horas": horas}


def energia_reciente(
    filas: list[Fila], ahora: datetime, capacidad_wh: Optional[float], reserva: float
) -> dict:
    """Respuesta de GET /domos/{id}/energia-reciente a partir de las filas (ver leer_filas)."""
    ahora = a_utc(ahora)
    con_potencia = [f for f in filas if f.tiene_potencia]
    if not con_potencia:
        return {"vigente": False, "fecha_ultima": None, "autonomia": None, "flujo": None}
    fin = con_potencia[-1].t
    vigente_desde = ahora - timedelta(seconds=VIGENCIA_S)
    if fin < vigente_desde:
        return {"vigente": False, "fecha_ultima": fin, "autonomia": None, "flujo": None}
    # El SoC actual, con la misma regla que /estado-general: el último no nulo y vigente.
    socs = [f.soc for f in filas if f.soc is not None and f.t >= vigente_desde]
    soc = socs[-1] if socs else None
    return {
        "vigente": True,
        "fecha_ultima": fin,
        "autonomia": autonomia_promedio(filas, fin, soc, capacidad_wh, reserva),
        "flujo": flujo_promedio(filas, fin),
    }
