"""
Simulador de telemetría del domo — publica por MQTT lecturas realistas
de batería, generación fotovoltaica y consumo, como lo haría el ESP32,
para probar el panel del frontend y el algoritmo de supervivencia (R-13)
sin hardware.

Publica en los mismos temas y con los mismos campos que espera
mqtt_listener.py (MAPEO_TIPOS), que es quien inserta las lecturas en la
base de datos — así que el listener tiene que estar corriendo:

    domo/{domo_id}/telemetria/bateria       voltaje, corriente, potencia,
                                            estado_carga_soc, temperatura,
                                            estado_bateria
    domo/{domo_id}/telemetria/fotovoltaica  voltaje, corriente,
                                            potencia_generada,
                                            energia_generada,
                                            estado_generacion
    domo/{domo_id}/telemetria/consumo       voltaje, corriente,
                                            potencia_consumida,
                                            energia_consumida

No envía `fecha_hora`: la asigna la base de datos al insertar (hora real
del servidor), igual que si viniera del ESP32 sin reloj.

Solo publica en temas de TELEMETRÍA. Nunca publica en
domo/+/comando/# — esos temas mueven la toma física real.

Dos escenarios (--escenario):

Los dos escenarios cruzan los umbrales reales del domo, para que R-13 corte y
reactive sus tomas sea cual sea la batería. Al arrancar leen de la base (solo
lectura) el apagado y la reactivación de las tomas activas no esenciales, y con eso
fijan el SoC mínimo (el apagado más bajo menos 10) y el máximo (la reactivación más
alta más 10). --soc-bajo y --soc-alto los fijan a mano. Con --sin-bd, o si la base
no responde y no se indicaron, se usan 10 y 90 en demo-r13, y en ciclo el SoC
depende solo del balance de energía.

"ciclo" (por defecto) — sistema de 12 V, ~500 Wp, banco de
--capacidad-ah (por defecto 100 Ah, la batería real del proyecto: CSB
HRL12390W):
- Un "día" completo dura --ciclo segundos reales (por defecto 900): la
  primera mitad es de día (curva solar senoidal con nubosidad aleatoria)
  y la segunda de noche (generación 0). Arranca al amanecer.
- El tiempo simulado avanza 86400/ciclo veces más rápido que el real, y
  el cambio de SoC se calcula con ese tiempo simulado (para ver un día
  completo en pocos minutos).
- energia_generada/energia_consumida, en cambio, son la energía del
  intervalo REAL entre publicaciones: potencia × intervalo_s / 3600 (Wh).
  Así los totales de R-14 son coherentes con las potencias publicadas
  (p. ej. 65 W de media durante 1 h ≈ 65 Wh).
- Consumo: paseo aleatorio entre 30 y 120 W.
- SoC: sube con balance positivo y baja con balance negativo, limitado a
  5–100 %. Con umbrales conocidos el mínimo es el SoC mínimo de arriba, y el cambio
  se acelera lo necesario para que hasta el día y la noche más flojos recorran el
  rango completo. Por eso el SoC no se corresponde exactamente con la energía
  publicada en ese caso.

"demo-r13" — perfil DETERMINISTA y repetible (sin ningún valor
aleatorio) para demostrar R-13; el SoC es una función lineal del tiempo
transcurrido, no de la energía:
  DESCARGA      --t-descarga s (180): sin generación, consumo constante;
                el SoC baja linealmente de --soc-inicial (el SoC máximo) al mínimo.
  BAJO          --t-bajo s (120): SoC fijo en el mínimo; la generación débil
                compensa el consumo reducido (balance 0).
  RECUPERACIÓN  --t-recuperacion s (180): generación alta; el SoC sube
                linealmente hasta el máximo.
  FIN           SoC fijo en el máximo (balance 0); se avisa una vez
                "Escenario terminado" y sigue publicando.
R-13 apaga las tomas en la DESCARGA y las reactiva durante la RECUPERACIÓN. Si las
tomas del domo tienen id_fisico, eso mueve el hardware real.

En ambos: voltaje de batería ≈ 11,8 V (vacía) a 13,0 V (llena);
corriente/potencia de batería positivas = cargando, negativas =
descargando (ver docs/Requisitos/Variables.md).

Protección contra mezclar fuentes: --domo es obligatorio (no hay domo por
defecto), y antes de publicar se consulta en la base, solo leyendo, la última
lectura de batería, fotovoltaica y consumo del domo. Como el simulador todavía
no publicó nada, una lectura de los últimos VENTANA_OTRA_FUENTE_MIN minutos vino
de otra fuente (el ESP32 real, otro simulador o uno anterior): se niega a correr,
porque el listener guardaría las dos mezcladas en las mismas tablas y R-13
movería tomas reales con un SoC inventado. También se niega si no puede
comprobarlo (--sin-bd o la base no responde). --forzar lo salta todo.

Uso (desde backend/, con el .venv activado):

    python tools/simulador_telemetria.py --domo 2
    python tools/simulador_telemetria.py --domo 2 --intervalo 3 --ciclo 120
    python tools/simulador_telemetria.py --domo 2 --soc-inicial 25   # probar R-13
    python tools/simulador_telemetria.py --domo 2 --escenario demo-r13
    python tools/simulador_telemetria.py --domo 2 --escenario demo-r13 --soc-bajo 40 --soc-alto 85
"""

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

TIPOS_TELEMETRIA = ("bateria", "fotovoltaica", "consumo")

POTENCIA_PICO_W = 500.0
EFICIENCIA_SISTEMA = 0.85  # pérdidas de controlador, cableado, temperatura
VOLTAJE_PANEL_MPP = 18.0
VOLTAJE_AC_CARGAS = 120.0
CONSUMO_MIN_W = 30.0
CONSUMO_MAX_W = 120.0
SOC_MIN = 5.0
SOC_MAX = 100.0
VOLTAJE_BATERIA_VACIA = 11.8
VOLTAJE_BATERIA_LLENA = 13.0
TEMPERATURA_BASE_C = 25.0
SEGUNDOS_POR_DIA = 86400

# Escenario demo-r13 (valores fijos, sin aleatoriedad).
DEMO_SOC_BAJO = 10.0
DEMO_SOC_FINAL = 90.0
DEMO_CONSUMO_W = 80.0  # DESCARGA y RECUPERACIÓN
DEMO_CONSUMO_BAJO_W = 40.0  # BAJO: R-13 ya cortó las tomas no esenciales
DEMO_GENERACION_ALTA_W = 380.0  # RECUPERACIÓN

MARGEN_UMBRALES = 10.0  # cuánto pasa el SoC más allá de los umbrales del domo
# Telemetría más reciente que esto = el domo tiene otra fuente activa.
VENTANA_OTRA_FUENTE_MIN = 10
SOC_INICIAL_CICLO = 70.0
# Para el factor de aceleración de "ciclo": lo mínimo que sube o baja el SoC en el
# día y la noche más flojos. 0,55 es la nubosidad más densa y 2/pi el valor medio de
# la curva solar; el margen cubre que el consumo y las nubes son aleatorios.
HORAS_MEDIO_DIA = 12.0
NUBOSIDAD_MINIMA = 0.55
MARGEN_ACELERACION = 1.5


def tema_telemetria(domo_id: int, tipo: str) -> str:
    if tipo not in TIPOS_TELEMETRIA:
        raise ValueError(f"Tipo de telemetría no permitido: {tipo!r}")
    return f"domo/{domo_id}/telemetria/{tipo}"


def _voltaje_bateria(soc: float, balance_w: float) -> float:
    """≈11,8 V (SoC mínimo) a 13,0 V (lleno), con un pequeño desplazamiento
    bajo carga/descarga, sin salir del rango."""
    reposo = VOLTAJE_BATERIA_VACIA + (VOLTAJE_BATERIA_LLENA - VOLTAJE_BATERIA_VACIA) * (soc - SOC_MIN) / (SOC_MAX - SOC_MIN)
    ajuste = 0.1 if balance_w > 0 else -0.05 if balance_w < 0 else 0.0
    return min(VOLTAJE_BATERIA_LLENA, max(VOLTAJE_BATERIA_VACIA, reposo + ajuste))


def _estado_bateria(soc: float, balance_generacion: float, balance_bateria: float) -> str:
    if soc >= SOC_MAX and balance_generacion >= 0:
        return "cargada"
    if balance_bateria > 0:
        return "cargando"
    if balance_bateria < 0:
        return "descargando"
    return "en_reposo"


def armar_lecturas(
    soc: float,
    generada: float,
    consumida: float,
    balance_bateria: float,
    horas: float,
    voltaje_bat: float,
    voltaje_panel: float,
    voltaje_ac: float,
    temperatura: float,
) -> dict[str, dict]:
    """Payloads de los tres temas, con los campos de MAPEO_TIPOS de
    mqtt_listener.py. `horas` es la duración del intervalo para la energía."""
    return {
        "bateria": {
            "voltaje": round(voltaje_bat, 2),
            "corriente": round(balance_bateria / voltaje_bat, 2),
            "potencia": round(balance_bateria, 1),
            "estado_carga_soc": round(soc, 1),
            "temperatura": round(temperatura, 1),
            "estado_bateria": _estado_bateria(soc, generada - consumida, balance_bateria),
        },
        "fotovoltaica": {
            "voltaje": round(voltaje_panel, 2),
            "corriente": round(generada / voltaje_panel, 2) if voltaje_panel > 0 else 0.0,
            "potencia_generada": round(generada, 1),
            "energia_generada": round(generada * horas, 4),
            "estado_generacion": "generando" if generada > 0 else "sin_generacion",
        },
        "consumo": {
            "voltaje": round(voltaje_ac, 1),
            "corriente": round(consumida / VOLTAJE_AC_CARGAS, 2),
            "potencia_consumida": round(consumida, 1),
            "energia_consumida": round(consumida * horas, 4),
        },
    }


class SimuladorCiclo:
    """Escenario "ciclo": día/noche acelerado con valores aleatorios."""

    def __init__(
        self,
        soc_inicial: float,
        capacidad_ah: float,
        ciclo_s: float,
        intervalo_s: float,
        limites: tuple[float, float] | None = None,
    ):
        self.soc = soc_inicial
        self.capacidad_wh = capacidad_ah * 12.0
        self.soc_piso = limites[0] if limites else SOC_MIN
        self.aceleracion = self._aceleracion_para(limites) if limites else 1.0
        self.ciclo_s = ciclo_s
        self.horas_simuladas_por_paso = intervalo_s * (SEGUNDOS_POR_DIA / ciclo_s) / 3600.0  # para el SoC
        self.horas_reales_por_paso = intervalo_s / 3600.0  # para energia_generada/consumida
        self.consumo_w = random.uniform(50.0, 80.0)
        self.nubosidad = 1.0
        self.inicio = time.monotonic()

    def _aceleracion_para(self, limites: tuple[float, float]) -> float:
        """Factor sobre el cambio de SoC para recorrer el rango en el peor caso."""
        generacion_floja = POTENCIA_PICO_W * EFICIENCIA_SISTEMA * (2 / math.pi) * NUBOSIDAD_MINIMA
        balance_mas_flojo = min(CONSUMO_MIN_W, generacion_floja - CONSUMO_MAX_W)
        cambio_minimo = balance_mas_flojo * HORAS_MEDIO_DIA / self.capacidad_wh * 100.0
        return max(1.0, MARGEN_ACELERACION * (limites[1] - limites[0]) / cambio_minimo)

    def _fase_del_dia(self) -> float:
        """0 = amanecer, 0.5 = atardecer, 1 = amanecer siguiente."""
        return ((time.monotonic() - self.inicio) % self.ciclo_s) / self.ciclo_s

    def _generacion_w(self, fase: float) -> float:
        if fase >= 0.5:
            return 0.0
        irradiancia = math.sin(math.pi * fase / 0.5)
        # Nubes: factor que deriva lentamente entre 0,55 y 1.
        self.nubosidad = min(1.0, max(0.55, self.nubosidad + random.uniform(-0.08, 0.08)))
        return POTENCIA_PICO_W * EFICIENCIA_SISTEMA * irradiancia * self.nubosidad

    def _consumo_w(self) -> float:
        self.consumo_w = min(CONSUMO_MAX_W, max(CONSUMO_MIN_W, self.consumo_w + random.uniform(-15.0, 15.0)))
        return self.consumo_w

    def paso(self) -> tuple[str, dict[str, dict]]:
        fase = self._fase_del_dia()
        generada = self._generacion_w(fase)
        consumida = self._consumo_w()
        balance = generada - consumida

        # Con la batería llena el controlador recorta la carga; con la
        # batería en el mínimo, la potencia neta de la batería no puede
        # seguir bajando (corte por bajo voltaje).
        if (self.soc >= SOC_MAX and balance > 0) or (self.soc <= self.soc_piso and balance < 0):
            balance_bateria = 0.0
        else:
            balance_bateria = balance

        horas_simuladas = self.horas_simuladas_por_paso
        cambio = self.aceleracion * balance_bateria * horas_simuladas / self.capacidad_wh * 100.0
        self.soc = min(SOC_MAX, max(self.soc_piso, self.soc + cambio))

        lecturas = armar_lecturas(
            soc=self.soc,
            generada=generada,
            consumida=consumida,
            balance_bateria=balance_bateria,
            horas=self.horas_reales_por_paso,
            voltaje_bat=min(
                VOLTAJE_BATERIA_LLENA,
                max(VOLTAJE_BATERIA_VACIA, _voltaje_bateria(self.soc, balance_bateria) + random.uniform(-0.02, 0.02)),
            ),
            voltaje_panel=VOLTAJE_PANEL_MPP + random.uniform(-0.4, 0.4) if generada > 0 else 0.0,
            voltaje_ac=VOLTAJE_AC_CARGAS + random.uniform(-1.5, 1.5),
            temperatura=TEMPERATURA_BASE_C + random.uniform(-1.5, 1.5) + abs(balance_bateria) / 100.0,
        )
        return ("DÍA" if fase < 0.5 else "NOCHE"), lecturas


class SimuladorDemoR13:
    """Escenario "demo-r13": perfil determinista DESCARGA → BAJO →
    RECUPERACIÓN → FIN. El SoC depende solo del tiempo transcurrido."""

    def __init__(
        self,
        soc_inicial: float,
        t_descarga: float,
        t_bajo: float,
        t_recuperacion: float,
        intervalo_s: float,
        soc_bajo: float = DEMO_SOC_BAJO,
        soc_alto: float = DEMO_SOC_FINAL,
    ):
        self.soc_inicial = soc_inicial
        self.soc_bajo = soc_bajo
        self.soc_alto = soc_alto
        self.t_descarga = t_descarga
        self.t_bajo = t_bajo
        self.t_recuperacion = t_recuperacion
        self.horas_por_paso = intervalo_s / 3600.0
        self.inicio = time.monotonic()
        self.terminado = False

    def estado_en(self, t: float) -> tuple[str, float, float, float]:
        """(fase, soc, generada_w, consumida_w) a los `t` segundos del inicio."""
        if t < self.t_descarga:
            avance = t / self.t_descarga
            return "DESCARGA", self.soc_inicial + (self.soc_bajo - self.soc_inicial) * avance, 0.0, DEMO_CONSUMO_W
        t -= self.t_descarga
        if t < self.t_bajo:
            return "BAJO", self.soc_bajo, DEMO_CONSUMO_BAJO_W, DEMO_CONSUMO_BAJO_W
        t -= self.t_bajo
        if t < self.t_recuperacion:
            avance = t / self.t_recuperacion
            soc = self.soc_bajo + (self.soc_alto - self.soc_bajo) * avance
            return "RECUPERACIÓN", soc, DEMO_GENERACION_ALTA_W, DEMO_CONSUMO_W
        return "FIN", self.soc_alto, DEMO_CONSUMO_W, DEMO_CONSUMO_W

    def paso(self) -> tuple[str, dict[str, dict]]:
        fase, soc, generada, consumida = self.estado_en(time.monotonic() - self.inicio)
        balance = generada - consumida
        lecturas = armar_lecturas(
            soc=soc,
            generada=generada,
            consumida=consumida,
            balance_bateria=balance,
            horas=self.horas_por_paso,
            voltaje_bat=_voltaje_bateria(soc, balance),
            voltaje_panel=VOLTAJE_PANEL_MPP if generada > 0 else 0.0,
            voltaje_ac=VOLTAJE_AC_CARGAS,
            temperatura=TEMPERATURA_BASE_C,
        )
        return fase, lecturas


def limites_de_soc(umbrales: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(SoC mínimo, SoC máximo) que cruzan todos los pares (apagado, reactivación)."""
    if not umbrales:
        return None
    bajo = min(apagado for apagado, _ in umbrales) - MARGEN_UMBRALES
    alto = max(reactivacion for _, reactivacion in umbrales) + MARGEN_UMBRALES
    return max(SOC_MIN, bajo), min(SOC_MAX, alto)


def umbrales_del_domo(db, domo_id: int) -> list[tuple[float, float]]:
    """(apagado, reactivación) de las tomas que R-13 puede cortar, calculados con su
    categoría y la batería y el perfil del domo (modelo v2: no se guardan)."""
    from models import Dispositivo, Domo
    from servicios.categorias import ESENCIAL, perfil_de, tipo_bateria_de, umbrales_de

    domo = db.get(Domo, domo_id)
    if domo is None:
        return []
    tipo, perfil = tipo_bateria_de(domo), perfil_de(domo)
    tomas = (
        db.query(Dispositivo)
        .filter(
            Dispositivo.domo_id == domo_id,
            Dispositivo.tipo == "toma",
            Dispositivo.activo.is_(True),
            Dispositivo.categoria != ESENCIAL,
        )
        .all()
    )
    return [umbrales_de(t.categoria, tipo, perfil) for t in tomas]


def leer_umbrales_de_la_base(domo_id: int) -> list[tuple[float, float]]:
    """Vacío si no hay tomas o la base no responde: el simulador sirve sin ella."""
    sys.path.insert(0, str(BACKEND))
    try:
        from database import SessionLocal

        with SessionLocal() as db:
            return umbrales_del_domo(db, domo_id)
    except Exception as exc:
        print(f"No se pudieron leer los umbrales de la base ({type(exc).__name__}).")
        return []


def ultimas_lecturas(db, domo_id: int) -> dict[str, datetime]:
    """Fecha de la última lectura de cada tipo de telemetría del domo (solo lee). En v2
    es el bloque de 10 s más reciente con algún dato de ese tema en `mediciones`."""
    from sqlalchemy import func, select

    from models import Medicion
    from servicios.mediciones import tiene_datos

    resultado = {}
    for tipo in ("bateria", "fotovoltaica", "consumo"):
        ultima = db.execute(
            select(func.max(Medicion.fecha_hora)).where(Medicion.domo_id == domo_id, tiene_datos(tipo))
        ).scalar()
        if ultima is not None:
            # SQLite la devuelve sin zona; la base la guarda en UTC.
            resultado[tipo] = ultima if ultima.tzinfo else ultima.replace(tzinfo=timezone.utc)
    return resultado


def motivo_para_no_correr(db, domo_id: int, ahora: datetime) -> str | None:
    """None si el domo existe y no recibió telemetría en la ventana; si no, el porqué."""
    from models import Domo

    if db.get(Domo, domo_id) is None:
        return f"El domo {domo_id} no existe en la base de datos: el listener descartaría todo."
    limite = ahora - timedelta(minutes=VENTANA_OTRA_FUENTE_MIN)
    recientes = {tipo: fecha for tipo, fecha in ultimas_lecturas(db, domo_id).items() if fecha >= limite}
    if not recientes:
        return None
    detalle = ", ".join(f"{tipo} hace {max(0, int((ahora - fecha).total_seconds()))} s" for tipo, fecha in sorted(recientes.items()))
    return (
        f"El domo {domo_id} recibió telemetría en los últimos {VENTANA_OTRA_FUENTE_MIN} min ({detalle}). "
        "Otra fuente lo está alimentando (¿el ESP32 real?) y las lecturas se mezclarían en las mismas tablas. "
        f"Usa otro domo, o espera {VENTANA_OTRA_FUENTE_MIN} min si fue un simulador que ya detuviste."
    )


def comprobar_que_no_hay_otra_fuente(args: argparse.Namespace, ahora: datetime | None = None) -> None:
    """Termina el programa si el domo tiene otra fuente activa o no se puede comprobar,
    salvo con --forzar, que solo avisa."""
    if args.sin_bd:
        motivo = "Con --sin-bd no se puede comprobar si el domo recibe telemetría de otra fuente."
    else:
        sys.path.insert(0, str(BACKEND))
        try:
            from database import SessionLocal

            with SessionLocal() as db:
                motivo = motivo_para_no_correr(db, args.domo, ahora or datetime.now(timezone.utc))
        except Exception as exc:
            # Solo el tipo: el texto de un error de conexión puede traer la URL con la clave.
            motivo = f"No se pudo consultar la base ({type(exc).__name__}) para comprobar si el domo recibe telemetría de otra fuente."
    if motivo is None:
        return
    if args.forzar:
        print(f"ATENCIÓN (--forzar): {motivo}")
        return
    sys.exit(f"{motivo}\nNo se publicó nada. Si estás seguro, repite con --forzar.")


def resolver_limites(args: argparse.Namespace) -> tuple[float, float] | None:
    """Lo indicado a mano manda; lo que falte sale de los umbrales de la base."""
    desde_la_base = None
    if not args.sin_bd and (args.soc_bajo is None or args.soc_alto is None):
        umbrales = leer_umbrales_de_la_base(args.domo)
        desde_la_base = limites_de_soc(umbrales)
        if desde_la_base:
            print(f"Umbrales del domo {args.domo} (apagado/reactivación): {umbrales} → SoC {desde_la_base[0]:g}–{desde_la_base[1]:g} %.")

    bajo = args.soc_bajo if args.soc_bajo is not None else (desde_la_base[0] if desde_la_base else None)
    alto = args.soc_alto if args.soc_alto is not None else (desde_la_base[1] if desde_la_base else None)
    if bajo is None and alto is None:
        return None
    limites = (DEMO_SOC_BAJO if bajo is None else bajo, DEMO_SOC_FINAL if alto is None else alto)
    if limites[0] >= limites[1]:
        sys.exit(f"El SoC mínimo ({limites[0]:g}) debe ser menor que el máximo ({limites[1]:g}).")
    return limites


def parsear_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulador de telemetría MQTT del domo (batería, fotovoltaica, consumo).")
    parser.add_argument("--escenario", choices=("ciclo", "demo-r13"), default="ciclo",
                        help="ciclo: día/noche aleatorio (por defecto); demo-r13: perfil determinista para demostrar R-13")
    parser.add_argument("--domo", type=int, required=True,
                        help="domo_id destino, obligatorio. Nunca uno cuyo ESP32 publica telemetría real")
    parser.add_argument("--forzar", action="store_true",
                        help=f"publica aunque el domo haya recibido telemetría de otra fuente en los últimos "
                             f"{VENTANA_OTRA_FUENTE_MIN} min, o aunque no se pueda comprobar")
    parser.add_argument("--intervalo", type=float, default=5.0, help="segundos entre publicaciones (por defecto 5)")
    parser.add_argument("--soc-inicial", type=float, default=None,
                        help="SoC inicial en %% (5–100; por defecto el SoC máximo, o 70 en ciclo sin umbrales conocidos)")
    parser.add_argument("--soc-bajo", type=float, default=None,
                        help="SoC mínimo en %%; por defecto el apagado más bajo del domo menos 10")
    parser.add_argument("--soc-alto", type=float, default=None,
                        help="SoC máximo en %%; por defecto la reactivación más alta del domo más 10")
    parser.add_argument("--sin-bd", action="store_true",
                        help="no leer la base de datos (umbrales ni comprobación de otra fuente); exige --forzar")
    grupo_ciclo = parser.add_argument_group("escenario ciclo")
    grupo_ciclo.add_argument("--ciclo", type=float, default=900.0, help="segundos reales que dura un día simulado (por defecto 900)")
    grupo_ciclo.add_argument("--capacidad-ah", type=float, default=100.0, help="capacidad del banco de 12 V en Ah (por defecto 100, la CSB HRL12390W)")
    grupo_demo = parser.add_argument_group("escenario demo-r13")
    grupo_demo.add_argument("--t-descarga", type=float, default=180.0, help="segundos de la fase DESCARGA (por defecto 180)")
    grupo_demo.add_argument("--t-bajo", type=float, default=120.0, help="segundos de la fase BAJO (por defecto 120)")
    grupo_demo.add_argument("--t-recuperacion", type=float, default=180.0, help="segundos de la fase RECUPERACIÓN (por defecto 180)")
    args = parser.parse_args(argv)

    positivos = ("intervalo", "ciclo", "capacidad_ah", "t_descarga", "t_bajo", "t_recuperacion")
    if any(getattr(args, nombre) <= 0 for nombre in positivos):
        parser.error("--intervalo, --ciclo, --capacidad-ah, --t-descarga, --t-bajo y --t-recuperacion deben ser mayores que 0.")
    for nombre in ("soc_inicial", "soc_bajo", "soc_alto"):
        valor = getattr(args, nombre)
        if valor is not None and not SOC_MIN <= valor <= SOC_MAX:
            parser.error(f"--{nombre.replace('_', '-')} debe estar entre {SOC_MIN:g} y {SOC_MAX:g}.")
    return args


def main() -> None:
    args = parsear_argumentos()
    comprobar_que_no_hay_otra_fuente(args)
    limites = resolver_limites(args)
    if args.soc_inicial is None:
        if limites:
            args.soc_inicial = limites[1]
        else:
            args.soc_inicial = DEMO_SOC_FINAL if args.escenario == "demo-r13" else SOC_INICIAL_CICLO

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT)
    except OSError as exc:
        sys.exit(f"No se pudo conectar a Mosquitto en {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}: {exc}")
    client.loop_start()

    if args.escenario == "demo-r13":
        soc_bajo, soc_alto = limites or (DEMO_SOC_BAJO, DEMO_SOC_FINAL)
        simulador = SimuladorDemoR13(
            args.soc_inicial, args.t_descarga, args.t_bajo, args.t_recuperacion, args.intervalo, soc_bajo, soc_alto
        )
        detalle = (
            f"escenario demo-r13: descarga {args.t_descarga:g} s → bajo {args.t_bajo:g} s → "
            f"recuperación {args.t_recuperacion:g} s, SoC {args.soc_inicial:g} → {soc_bajo:g} → {soc_alto:g} %"
        )
    else:
        simulador = SimuladorCiclo(args.soc_inicial, args.capacidad_ah, args.ciclo, args.intervalo, limites)
        detalle = f"escenario ciclo: día de {args.ciclo:g} s, SoC inicial {args.soc_inicial:g} %"
    print(
        f"Simulando domo {args.domo} → {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} "
        f"(intervalo {args.intervalo:g} s, {detalle}). Ctrl+C para salir."
    )

    aviso_fin_mostrado = False
    try:
        while True:
            hora = time.strftime("%H:%M:%S")
            fase, lecturas = simulador.paso()
            for tipo, payload in lecturas.items():
                tema = tema_telemetria(args.domo, tipo)
                client.publish(tema, json.dumps(payload), qos=1)
                print(f"{hora}  [{fase}]  {tema}  {json.dumps(payload, ensure_ascii=False)}")
            if fase == "FIN" and not aviso_fin_mostrado:
                print(f"{hora}  [FIN]  Escenario terminado: el SoC queda en {simulador.soc_alto:g} %. Ctrl+C para salir.")
                aviso_fin_mostrado = True
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\nSimulador detenido.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
