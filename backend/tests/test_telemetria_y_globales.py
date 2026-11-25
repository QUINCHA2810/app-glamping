"""Límites de telemetría (API y listener), umbrales "globales" del domo y simulador.

Modelo v2: las lecturas se guardan en `mediciones` (servicios/mediciones.py). Los
umbrales globales ya no se configuran: son el preset confort, calculado."""

import argparse
import importlib.util
import random
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Medicion
from schemas import LIMITES_TELEMETRIA
from servicios.mediciones import como_lectura
from tests.base import PruebaApi, importar_listener

# Lo que cada ruta necesita además del campo que se prueba.
CUERPO_BASE = {"/lecturas-sensores": {"variable_medida": "frecuencia"}}

CAMPOS_POR_RUTA = {
    "/lecturas-bateria": ("voltaje", "corriente", "potencia", "estado_carga_soc", "temperatura"),
    "/lecturas-fotovoltaicas": ("voltaje", "corriente", "potencia_generada", "energia_generada"),
    "/lecturas-consumo": ("voltaje", "corriente", "potencia_consumida", "energia_consumida"),
    "/lecturas-sensores": ("valor",),
}


class LimitesEnLaApi(PruebaApi):
    def post(self, ruta, cuerpo):
        return self.client.post(self.ruta(ruta), json={**CUERPO_BASE.get(ruta, {}), **cuerpo}, headers=self.auth())

    def test_todos_los_campos_tienen_limite(self):
        campos = {campo for lista in CAMPOS_POR_RUTA.values() for campo in lista}
        self.assertEqual(campos, set(LIMITES_TELEMETRIA))

    def test_fuera_de_rango_da_422_y_en_el_limite_se_guarda(self):
        for ruta, campos in CAMPOS_POR_RUTA.items():
            for campo in campos:
                minimo, maximo = LIMITES_TELEMETRIA[campo]
                with self.subTest(ruta=ruta, campo=campo):
                    for valor in (1e300, -1e300, maximo + 1, minimo - 1):
                        self.assertEqual(self.post(ruta, {campo: valor}).status_code, 422, valor)
                    for valor in (minimo, maximo):
                        respuesta = self.post(ruta, {campo: valor})
                        self.assertEqual(respuesta.status_code, 201, respuesta.text)
                        self.assertEqual(respuesta.json()[campo], valor)

    def test_un_valor_diminuto_se_guarda_como_cero(self):
        respuesta = self.post("/lecturas-bateria", {"corriente": 1e-300})
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.json()["corriente"], 0)

    def test_valores_de_la_alerta(self):
        base = {"tipo_alerta": "sobrecarga", "nivel_prioridad": "critico", "descripcion": "x"}
        for campo in ("valor_detectado", "valor_limite"):
            with self.subTest(campo=campo):
                self.assertEqual(self.post("/alertas", {**base, campo: 1e300}).status_code, 422)
                self.assertEqual(self.post("/alertas", {**base, campo: 12.5}).status_code, 201)

    def test_umbral_diminuto_no_da_500(self):
        # v2: los umbrales ya no se editan; tampoco un valor diminuto da 500.
        toma = self.toma()
        for cuerpo in ({"umbral_apagado_soc": 40, "umbral_reactivacion_soc": 50 + 1e-300}, {"umbral_apagado_soc": 1e-300}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch_dispositivo(toma, cuerpo).status_code, 422)


class LimitesEnElListener(PruebaApi):
    def setUp(self):
        super().setUp()
        self.listener = importar_listener()
        sesion = self.db.get_bind()
        from sqlalchemy.orm import sessionmaker

        parche = patch.object(self.listener, "SesionListener", sessionmaker(bind=sesion, autoflush=False, expire_on_commit=False))
        parche.start()
        self.addCleanup(parche.stop)
        self.listener._domos_conocidos.clear()

    def insertar(self, tipo, payload):
        campos, aceptados = self.listener.MAPEO_TIPOS[tipo]
        return self.listener._insertar_lectura(self.domo_a.id, tipo, campos, aceptados, payload)

    def bateria(self, **campos):
        """La lectura de batería tal como la devuelve la API (nombres de v1)."""
        self.assertTrue(self.insertar("bateria", campos))
        self.db.expire_all()
        medicion = self.db.query(Medicion).order_by(Medicion.fecha_hora.desc()).first()
        return como_lectura(medicion, "bateria")

    def test_absurdo_queda_null_con_warning_y_la_lectura_se_conserva(self):
        for campo in ("voltaje", "corriente", "potencia", "temperatura"):
            for valor in (1e300, -1e300, float("inf"), float("nan"), "abc", True, [1]):
                with self.subTest(campo=campo, valor=valor):
                    with self.assertLogs("mqtt_listener", "WARNING") as registro:
                        lectura = self.bateria(**{campo: valor, "estado_carga_soc": 80})
                    self.assertIsNone(lectura[campo])
                    self.assertEqual(lectura["estado_carga_soc"], 80)
                    self.assertIn(campo, registro.output[0])

    def test_otros_tipos_de_lectura(self):
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.assertTrue(self.insertar("consumo", {"potencia_consumida": 1e300, "voltaje": 120}))
        self.db.expire_all()
        fila = self.db.query(Medicion).one()
        self.assertEqual((fila.potencia_consumo_w, fila.voltaje_ac_v), (None, 120))

    def test_valores_validos_no_cambian(self):
        with self.assertNoLogs("mqtt_listener", "WARNING"):
            lectura = self.bateria(voltaje=12.5, corriente=-8, temperatura="25.5", estado_carga_soc=50)
        self.assertEqual((lectura["voltaje"], lectura["corriente"], lectura["temperatura"]), (12.5, -8, 25.5))

    def test_diminuto_es_cero_sin_warning(self):
        with self.assertNoLogs("mqtt_listener", "WARNING"):
            lectura = self.bateria(corriente=1e-300, estado_carga_soc=1e-300)
        self.assertEqual((lectura["corriente"], lectura["estado_carga_soc"]), (0, 0))

    def test_el_margen_del_soc_sigue_igual(self):
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.assertEqual(self.bateria(estado_carga_soc=103)["estado_carga_soc"], 100)
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.assertIsNone(self.bateria(estado_carga_soc=1e300)["estado_carga_soc"])


class UmbralesGlobales(PruebaApi):
    """v2: lo que la API sigue llamando umbrales globales es el preset confort de la
    batería y el perfil; ya no se guardan ni se configuran."""

    def config(self):
        return self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()

    def put(self, cuerpo):
        return self.client.put(self.ruta("/configuracion"), json=cuerpo, headers=self.auth())

    def globales(self):
        datos = self.config()
        return datos["umbral_soc_bajo"], datos["umbral_soc_recuperacion"]

    def test_siguen_el_preset_confort_de_la_bateria(self):
        self.assertEqual(self.globales(), (35, 45))
        self.poner_bateria("agm")
        self.assertEqual(self.globales(), (65, 75))

    def test_r13_usa_el_preset_de_agm(self):
        self.poner_bateria("agm")
        self.toma("t")
        self.soc(60)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        self.soc(74)
        self.assertEqual(self.evaluar(), [])
        self.soc(75)
        self.assertEqual(self.evaluar(), [("t", "encender")])

    def test_put_de_umbrales_da_422(self):
        self.poner_bateria("agm")
        for cuerpo in ({"umbral_soc_bajo": 50, "umbral_soc_recuperacion": 60}, {"umbral_soc_recuperacion": 80}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.put(cuerpo).status_code, 422)
        self.assertEqual(self.globales(), (65, 75))

    def test_el_put_rechazado_no_cambia_la_bateria(self):
        self.assertEqual(self.put({"bateria": {"tipo": "agm", "capacidad_wh": 1}, "umbral_soc_bajo": 30}).status_code, 422)
        self.assertIsNone(self.config()["bateria"])

    def test_categorias_informa_las_reservas(self):
        datos = self.client.get(self.ruta("/categorias"), headers=self.auth()).json()
        self.assertEqual(datos["reservas_por_bateria"], {"lifepo4": 20, "agm": 50})


class CrearConIdFisico(PruebaApi):
    def test_opcional_y_unico(self):
        primera = self.crear({"tipo": "toma", "nombre": "A", "id_fisico": " TUYA-1 "})
        self.assertEqual(primera.json()["id_fisico"], "TUYA-1")
        self.assertEqual(self.crear({"tipo": "toma", "nombre": "B", "id_fisico": "TUYA-1"}).status_code, 409)
        self.assertEqual(self.crear({"tipo": "toma", "nombre": "B", "id_fisico": "  "}).status_code, 422)
        self.assertIsNone(self.crear({"tipo": "toma", "nombre": "C"}).json()["id_fisico"])


def _cargar_simulador():
    ruta = Path(__file__).resolve().parent.parent / "tools" / "simulador_telemetria.py"
    spec = importlib.util.spec_from_file_location("simulador_telemetria", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


simulador = _cargar_simulador()
UMBRALES_DOMO_1 = [(65.0, 75.0), (50.0, 60.0)]


class LimitesDelSimulador(PruebaApi):
    def test_cruzan_los_umbrales_con_margen(self):
        self.assertEqual(simulador.limites_de_soc(UMBRALES_DOMO_1), (40, 85))
        self.assertEqual(simulador.limites_de_soc([(20, 30)]), (10, 40))
        self.assertEqual(simulador.limites_de_soc([(10, 95)]), (5, 100))
        self.assertIsNone(simulador.limites_de_soc([]))

    def test_lee_los_calculados_de_las_tomas_que_se_pueden_cortar(self):
        self.poner_bateria("agm")
        self.toma("confort")
        self.toma("importante", categoria="importante")
        self.toma("inactiva", activo=False, desactivado_en=datetime.now(timezone.utc))
        self.toma("esencial", categoria="esencial")
        self.bombillo()
        self.assertEqual(simulador.umbrales_del_domo(self.db, self.domo_a.id), [(65, 75), (50, 60)])
        self.assertEqual(simulador.umbrales_del_domo(self.db, 999), [])

    def args(self, **campos):
        return argparse.Namespace(**{"sin_bd": False, "soc_bajo": None, "soc_alto": None, "domo": 1, **campos})

    def test_lo_indicado_a_mano_manda(self):
        with patch.object(simulador, "leer_umbrales_de_la_base", return_value=UMBRALES_DOMO_1) as leer:
            self.assertEqual(simulador.resolver_limites(self.args()), (40, 85))
            self.assertEqual(simulador.resolver_limites(self.args(soc_bajo=30)), (30, 85))
            leer.reset_mock()
            self.assertEqual(simulador.resolver_limites(self.args(soc_bajo=30, soc_alto=80)), (30, 80))
            leer.assert_not_called()
            self.assertIsNone(simulador.resolver_limites(self.args(sin_bd=True)))
            leer.assert_not_called()

    def test_sin_tomas_ni_parametros_no_hay_limites(self):
        with patch.object(simulador, "leer_umbrales_de_la_base", return_value=[]):
            self.assertIsNone(simulador.resolver_limites(self.args()))

    def test_minimo_mayor_que_maximo_termina_con_error(self):
        with self.assertRaises(SystemExit):
            simulador.resolver_limites(self.args(soc_bajo=90, soc_alto=80))


class EscenariosDelSimulador(TestCase):
    def assert_cruza(self, soc_por_tiempo, umbrales):
        apagados = [a for a, _ in umbrales]
        reactivaciones = [r for _, r in umbrales]
        self.assertLess(min(soc_por_tiempo), min(apagados))
        self.assertGreaterEqual(max(soc_por_tiempo), max(reactivaciones))

    def test_demo_cruza_y_termina_en_el_maximo(self):
        for umbrales in (UMBRALES_DOMO_1, [(20, 30), (35, 45), (50, 60)]):
            bajo, alto = simulador.limites_de_soc(umbrales)
            demo = simulador.SimuladorDemoR13(alto, 180, 120, 180, 5, bajo, alto)
            estados = [demo.estado_en(t) for t in range(0, 600, 5)]
            self.assert_cruza([soc for _, soc, _, _ in estados], umbrales)
            # Baja por todos los umbrales antes de subir por todos.
            descarga = [soc for fase, soc, _, _ in estados if fase == "DESCARGA"]
            recuperacion = [soc for fase, soc, _, _ in estados if fase == "RECUPERACIÓN"]
            self.assertEqual(descarga, sorted(descarga, reverse=True))
            self.assertEqual(recuperacion, sorted(recuperacion))
            self.assertEqual(estados[-1][:2], ("FIN", alto))

    def dias(self, semilla, umbrales, capacidad_ah=100.0, ciclo=900.0, intervalo=5.0):
        """SoC por día simulado, con el reloj del simulador avanzando a mano."""
        random.seed(semilla)
        limites = simulador.limites_de_soc(umbrales)
        ahora = [0.0]
        with patch.object(simulador.time, "monotonic", lambda: ahora[0]):
            ciclo_sim = simulador.SimuladorCiclo(limites[1], capacidad_ah, ciclo, intervalo, limites)
            pasos = int(ciclo / intervalo)
            for _ in range(4):
                socs = []
                for _ in range(pasos):
                    _, lecturas = ciclo_sim.paso()
                    socs.append(lecturas["bateria"]["estado_carga_soc"])
                    ahora[0] += intervalo
                yield socs

    def test_ciclo_cruza_todos_los_dias_con_cualquier_azar(self):
        casos = [
            (UMBRALES_DOMO_1, 100.0, 900.0),
            (UMBRALES_DOMO_1, 100.0, 120.0),
            ([(20, 30), (35, 45), (50, 60)], 100.0, 900.0),
            (UMBRALES_DOMO_1, 300.0, 900.0),
            (UMBRALES_DOMO_1, 30.0, 900.0),
        ]
        for umbrales, capacidad, ciclo in casos:
            for semilla in range(60):
                with self.subTest(umbrales=umbrales, capacidad=capacidad, ciclo=ciclo, semilla=semilla):
                    for socs in self.dias(semilla, umbrales, capacidad, ciclo):
                        self.assert_cruza(socs, umbrales)

    def test_sin_limites_el_ciclo_sigue_igual_que_antes(self):
        ciclo_sim = simulador.SimuladorCiclo(70.0, 100.0, 900.0, 5.0)
        self.assertEqual((ciclo_sim.soc_piso, ciclo_sim.aceleracion), (simulador.SOC_MIN, 1.0))

    def test_las_lecturas_respetan_los_limites_de_telemetria(self):
        for fase_lecturas in self.dias(1, UMBRALES_DOMO_1):
            self.assertTrue(all(0 <= soc <= 100 for soc in fase_lecturas))
