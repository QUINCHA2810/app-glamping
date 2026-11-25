"""Autonomía con potencia media de 15 min y flujo con promedio de 60 s
(servicios/energia_reciente.py, GET /domos/{id}/energia-reciente)."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Medicion
from servicios import energia_reciente as er
from servicios.energia_reciente import Fila
from tests.base import PruebaApi

RAIZ = Path(__file__).resolve().parents[2]
SENSORES_CONFIG = RAIZ / "firmware" / "domo_nodo" / "sensores_config.h"

AHORA = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)
CAPACIDAD = 1200.0
RESERVA = 50.0
SOC = 80.0
ENERGIA = CAPACIDAD * (SOC - RESERVA) / 100  # 360 Wh


def serie(n=90, fin=AHORA, fv=0.0, consumo=100.0, bateria=-120.0, soc=SOC, cambios=None):
    """n filas de 10 s que terminan en `fin`. `cambios`: {índice desde el final: {campo: valor}}."""
    filas = []
    for i in range(n):
        atras = n - 1 - i
        valores = {"fv": fv, "consumo": consumo, "bateria": bateria, "soc": soc}
        valores.update((cambios or {}).get(atras, {}))
        filas.append(Fila(fin - timedelta(seconds=10 * atras), **valores))
    return filas


def calcular(filas, ahora=AHORA, capacidad=CAPACIDAD, reserva=RESERVA):
    return er.energia_reciente(filas, ahora, capacidad, reserva)


class Autonomia(TestCase):
    def test_carga_estable(self):
        r = calcular(serie())
        a = r["autonomia"]
        self.assertTrue(r["vigente"])
        self.assertEqual(a["tipo"], "horas")
        self.assertAlmostEqual(a["horas"], ENERGIA / 120)  # 3 h
        self.assertAlmostEqual(a["potencia_media_w"], -120)
        self.assertEqual((a["minutos"], a["ventana_min"]), (15.0, 15.0))

    def test_un_pico_de_10_s_casi_no_la_mueve(self):
        estable = calcular(serie())["autonomia"]["horas"]
        # Un bloque de 10 s a 2000 W en medio de la ventana (antes la hundía 16 veces).
        pico = calcular(serie(cambios={40: {"bateria": -2000.0, "consumo": 1900.0}}))["autonomia"]["horas"]
        self.assertAlmostEqual(pico, ENERGIA / (-(89 * -120 - 2000) / 90))
        self.assertGreater(pico / estable, 0.8)
        # Y el pico en el último bloque: la autonomía tampoco se desploma.
        al_final = calcular(serie(cambios={0: {"bateria": -2000.0}}))["autonomia"]["horas"]
        self.assertGreater(al_final / estable, 0.8)

    def test_bateria_cargando_da_cargando_y_sin_sol(self):
        # Sol: FV 400 W, la batería recibe +250 W en promedio. Carga DC = 400·0,95 − 250 = 130 W.
        a = calcular(serie(fv=400.0, bateria=250.0))["autonomia"]
        self.assertEqual(a["tipo"], "cargando")
        self.assertIsNone(a["horas"])
        self.assertAlmostEqual(a["carga_dc_media_w"], 400 * er.EFICIENCIA_MPPT - 250)
        self.assertAlmostEqual(a["horas_sin_sol"], ENERGIA / 130)
        self.assertFalse(a["sin_sol_supera_tope"])

    def test_sin_fv_cuenta_como_cero_solo_si_hay_bateria(self):
        filas = serie(fv=None, bateria=-100.0, cambios={0: {"bateria": None, "fv": 500.0}})
        a = calcular(filas)["autonomia"]
        # La fila sin batería no entra; las demás cuentan la FV como 0: carga DC = 100 W.
        self.assertAlmostEqual(a["carga_dc_media_w"], 100.0)
        self.assertAlmostEqual(a["minutos"], 89 * 10 / 60)

    def test_consumo_casi_cero_da_mas_del_tope(self):
        a = calcular(serie(consumo=1.0, bateria=-3.0))["autonomia"]
        self.assertEqual(a["tipo"], "mas_del_tope")
        self.assertIsNone(a["horas"])
        self.assertEqual(a["tope_horas"], 72.0)
        # Descarga de 6 W: 360 Wh / 6 W = 60 h → horas; con 12000 Wh serían 600 h → más de 72 h.
        self.assertEqual(calcular(serie(bateria=-6.0))["autonomia"]["tipo"], "horas")
        self.assertEqual(calcular(serie(bateria=-6.0), capacidad=12000)["autonomia"]["tipo"], "mas_del_tope")
        # Carga casi cero con sol: sin sol con carga DC muy baja también pasa del tope.
        a = calcular(serie(fv=10.0, bateria=6.0))["autonomia"]
        self.assertEqual(a["tipo"], "cargando")
        self.assertTrue(a["sin_sol_supera_tope"])
        self.assertIsNone(a["horas_sin_sol"])

    def test_casi_cero_con_sol_trae_sin_sol(self):
        # Con sol la batería casi no se mueve (−3 W): "más de 72 h", y aparte cuánto duraría
        # sin sol con la carga DC media = 50·0,95 + 3 = 50,5 W.
        a = calcular(serie(fv=50.0, bateria=-3.0))["autonomia"]
        self.assertEqual(a["tipo"], "mas_del_tope")
        self.assertAlmostEqual(a["carga_dc_media_w"], 50 * er.EFICIENCIA_MPPT + 3)
        self.assertAlmostEqual(a["horas_sin_sol"], ENERGIA / (50 * er.EFICIENCIA_MPPT + 3))
        # De noche (sin FV) la carga DC es la misma descarga: sin sol también pasa del tope.
        a = calcular(serie(fv=0.0, bateria=-3.0))["autonomia"]
        self.assertEqual((a["tipo"], a["horas_sin_sol"], a["sin_sol_supera_tope"]), ("mas_del_tope", None, True))

    def test_pocos_datos(self):
        a = calcular(serie(n=11))["autonomia"]  # 110 s < 2 min
        self.assertEqual(a["tipo"], "pocos_datos")
        self.assertIsNone(a["horas"])
        a = calcular(serie(n=12))["autonomia"]  # 2 min justos
        self.assertEqual(a["tipo"], "horas")
        self.assertEqual(a["minutos"], 2.0)

    def test_huecos_no_se_rellenan(self):
        # 3 min de datos, 8 min sin nada y 4 min más: 7 min con datos de una ventana de 15.
        filas = serie(n=24) + serie(n=18, fin=AHORA - timedelta(minutes=12))
        filas.sort(key=lambda f: f.t)
        a = calcular(filas)["autonomia"]
        self.assertEqual(a["tipo"], "horas")
        self.assertAlmostEqual(a["minutos"], 42 * 10 / 60)
        self.assertAlmostEqual(a["horas"], ENERGIA / 120)
        # Lo de antes de la ventana de 15 min no cuenta.
        viejas = serie(n=30, fin=AHORA - timedelta(minutes=16), bateria=-1000.0)
        self.assertAlmostEqual(calcular(sorted(viejas + serie(n=30), key=lambda f: f.t))["autonomia"]["horas"], ENERGIA / 120)

    def test_reserva_capacidad_y_sin_soc(self):
        self.assertEqual(calcular(serie(soc=50.0))["autonomia"]["tipo"], "reserva")
        self.assertEqual(calcular(serie(), capacidad=None)["autonomia"]["tipo"], "sin_capacidad")
        self.assertEqual(calcular(serie(soc=None))["autonomia"]["tipo"], "sin_datos")

    def test_eficiencia_igual_al_firmware(self):
        texto = SENSORES_CONFIG.read_text(encoding="utf-8")
        valor = re.search(r"#define\s+EFICIENCIA_MPPT\s+([0-9.]+)f?", texto).group(1)
        self.assertEqual(float(valor), er.EFICIENCIA_MPPT)


class Vigencia(TestCase):
    def test_sin_datos_vigentes_no_hay_autonomia_ni_flujo(self):
        filas = serie(fin=AHORA - timedelta(seconds=131))
        r = calcular(filas)
        self.assertEqual((r["vigente"], r["autonomia"], r["flujo"]), (False, None, None))
        self.assertEqual(r["fecha_ultima"], AHORA - timedelta(seconds=131))
        # A 120 s justos todavía vale, y las ventanas terminan en esa medición.
        r = calcular(serie(fin=AHORA - timedelta(seconds=120)))
        self.assertTrue(r["vigente"])
        self.assertEqual(r["autonomia"]["minutos"], 15.0)

    def test_sin_filas(self):
        self.assertEqual(calcular([]), {"vigente": False, "fecha_ultima": None, "autonomia": None, "flujo": None})
        solo_soc = [Fila(AHORA, soc=70.0)]
        self.assertFalse(calcular(solo_soc)["vigente"])

    def test_soc_viejo_no_vale(self):
        filas = serie(soc=None, cambios={20: {"soc": 80.0}})  # SoC de hace 200 s
        self.assertEqual(calcular(filas)["autonomia"]["tipo"], "sin_datos")


class Flujo(TestCase):
    def test_promedio_de_los_ultimos_60_s(self):
        # La FV pulsa entre 50 y 350 W: el promedio de 6 bloques es 200.
        cambios = {i: {"fv": 50.0 if i % 2 else 350.0} for i in range(6)}
        cambios[6] = {"fv": 5000.0}  # fuera del minuto: no cuenta
        f = calcular(serie(cambios=cambios))["flujo"]
        self.assertAlmostEqual(f["generada"], 200.0)
        self.assertAlmostEqual(f["consumida"], 100.0)
        self.assertAlmostEqual(f["bateria"], -120.0)

    def test_sin_dato_en_el_minuto_queda_en_null(self):
        filas = serie(cambios={i: {"consumo": None} for i in range(6)})
        self.assertIsNone(calcular(filas)["flujo"]["consumida"])
        self.assertIsNotNone(calcular(filas)["flujo"]["generada"])


class PerdidasDelInversor(TestCase):
    def test_caso_del_24_nov_1641(self):
        # PV 26,1 W + batería 120,2 W = 146,3 W DC; cargas 117,8 W AC.
        p = er.perdidas_inversor(26.1, 117.8, -120.2)
        self.assertAlmostEqual(p["entrada_inversor_w"], 146.3)
        self.assertAlmostEqual(p["perdidas_w"], 28.5)
        self.assertAlmostEqual(p["eficiencia_pct"], 80.52, places=2)

    def test_con_los_promedios_del_flujo(self):
        f = calcular(serie(fv=26.1, consumo=117.8, bateria=-120.2))["flujo"]
        self.assertAlmostEqual(f["perdidas_w"], 28.5)
        self.assertAlmostEqual(f["eficiencia_pct"], 80.5, places=1)

    def test_cargando_la_entrada_es_pv_menos_la_carga(self):
        p = er.perdidas_inversor(300.0, 150.0, 120.0)
        self.assertAlmostEqual(p["entrada_inversor_w"], 180.0)
        self.assertAlmostEqual(p["perdidas_w"], 30.0)
        self.assertAlmostEqual(p["eficiencia_pct"], 150 / 180 * 100)

    def test_entrada_menor_de_20_w_sin_eficiencia(self):
        p = er.perdidas_inversor(0.0, 12.0, -15.0)
        self.assertAlmostEqual(p["entrada_inversor_w"], 15.0)
        self.assertAlmostEqual(p["perdidas_w"], 3.0)
        self.assertIsNone(p["eficiencia_pct"])

    def test_mas_de_100_por_ciento_es_null_y_sin_perdidas_negativas(self):
        p = er.perdidas_inversor(0.0, 130.0, -120.0)
        self.assertEqual((p["perdidas_w"], p["eficiencia_pct"]), (0.0, None))

    def test_sin_datos(self):
        vacio = {"entrada_inversor_w": None, "perdidas_w": None, "eficiencia_pct": None}
        self.assertEqual(er.perdidas_inversor(26.1, 117.8, None), vacio)
        self.assertEqual(er.perdidas_inversor(None, 117.8, -120.2), vacio)
        p = er.perdidas_inversor(26.1, None, -120.2)
        self.assertEqual((p["perdidas_w"], p["eficiencia_pct"]), (None, None))
        self.assertAlmostEqual(p["entrada_inversor_w"], 146.3)


class Api(PruebaApi):
    def setUp(self):
        super().setUp()
        self.domo_a.bateria_tipo = "agm"
        self.domo_a.bateria_capacidad_wh = CAPACIDAD
        self.db.commit()
        reloj = patch("servicios.mediciones.ahora_utc", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def medir(self, n, bateria=-120.0, fin=AHORA - timedelta(seconds=10)):
        for f in serie(n=n, fin=fin, bateria=bateria):
            self.db.add(Medicion(
                domo_id=self.domo_a.id, fecha_hora=f.t, potencia_fv_w=f.fv,
                potencia_consumo_w=f.consumo, potencia_bateria_w=f.bateria, soc_pct=f.soc,
            ))
        self.db.commit()

    def pedir(self, usuario=None):
        return self.client.get(self.ruta("/energia-reciente"), headers=self.auth(usuario))

    def test_devuelve_autonomia_y_flujo(self):
        self.medir(100)  # más de 15 min: lee solo lo necesario
        r = self.pedir()
        self.assertEqual(r.status_code, 200, r.text)
        cuerpo = r.json()
        self.assertTrue(cuerpo["vigente"])
        self.assertEqual(cuerpo["autonomia"]["tipo"], "horas")
        self.assertAlmostEqual(cuerpo["autonomia"]["horas"], ENERGIA / 120)  # reserva AGM 50 %
        self.assertEqual(cuerpo["autonomia"]["minutos"], 15.0)
        self.assertEqual(cuerpo["flujo"], {
            "generada": 0.0, "consumida": 100.0, "bateria": -120.0,
            "entrada_inversor_w": 120.0, "perdidas_w": 20.0, "eficiencia_pct": 100 / 120 * 100,
        })
        self.assertTrue(cuerpo["fecha_ultima"].endswith(("Z", "+00:00")))

    def test_sin_mediciones_vigentes(self):
        self.medir(20, fin=AHORA - timedelta(minutes=5))
        cuerpo = self.pedir().json()
        self.assertEqual((cuerpo["vigente"], cuerpo["autonomia"], cuerpo["flujo"]), (False, None, None))

    def test_permisos_iguales_a_las_mediciones(self):
        self.medir(12)
        self.assertEqual(self.pedir(self.admin).status_code, 200)
        intruso = self.usuario("intruso@prueba.co")
        self.db.commit()
        self.assertEqual(self.pedir(intruso).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/energia-reciente")).status_code, 401)
