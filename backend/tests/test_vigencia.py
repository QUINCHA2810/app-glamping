"""Datos viejos no se usan como actuales (ronda DB-3, problema 1).

Un ESP32 sin sensores publica el SoC y el consumo en null. El Panel mostraba como
actuales los últimos valores con dato (de días atrás) y R-13 decidía con ese SoC viejo:
apagó una toma real que acababan de encender a mano. Ahora una medida solo es actual si
tiene como mucho mediciones.VIGENCIA_S (120 s); si no, el Panel la da como null y R-13
no corta ni reactiva.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)

from models import Accion, Dispositivo
from servicios import mediciones
from servicios.control_energetico import EvaluacionNoPosible, evaluar_control_energetico
from tests.base import PruebaApi, importar_listener

AHORA = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
VIEJO = AHORA - timedelta(days=10)
RECIENTE = AHORA - timedelta(seconds=30)


class ConReloj(PruebaApi):
    def setUp(self):
        super().setUp()
        reloj = patch("servicios.mediciones.ahora_utc", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def guardar(self, tema, valores, fecha):
        mediciones.guardar_medicion(self.db, self.domo_a.id, tema, valores, fecha)
        self.db.commit()

    def post_evaluar(self):
        return self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth())

    def acciones(self):
        self.db.expire_all()
        return self.db.query(Accion).count()


class R13ConSocViejo(ConReloj):
    def test_soc_viejo_no_corta(self):
        toma = self.toma("t")
        self.soc(20, fecha_hora=VIEJO)
        respuesta = self.post_evaluar()
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("2 minutos", respuesta.json()["detail"])
        self.assertIn("20 %", respuesta.json()["detail"])
        self.assertEqual(self.recargar(toma).estado, "encendido")
        self.assertEqual(self.acciones(), 0)
        self.assertEqual(self.alertas_bateria(), [])

    def test_esp32_sin_sensor_que_publica_null_no_revive_el_soc_viejo(self):
        """El caso del martes: SoC 51,1 de días atrás y después solo lecturas en null."""
        toma = self.toma("toma2", categoria="prescindible")
        self.soc(51.1, fecha_hora=VIEJO)
        self.guardar("bateria", {"estado_carga_soc": None, "voltaje": None}, RECIENTE)
        self.guardar("consumo", {"potencia_consumida": None}, RECIENTE)
        self.assertEqual(self.post_evaluar().status_code, 409)
        self.assertEqual(self.recargar(toma).estado, "encendido")
        self.assertEqual(self.acciones(), 0)

    def test_soc_viejo_no_reactiva(self):
        toma = self.toma("t")
        self.soc(20, fecha_hora=RECIENTE)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        # Se recupera, pero el dato envejece antes de la siguiente evaluación.
        with patch("servicios.mediciones.ahora_utc", return_value=AHORA + timedelta(minutes=10)):
            self.assertEqual(self.post_evaluar().status_code, 409)
        self.assertEqual(self.recargar(toma).estado, "apagado")

    def test_sin_ningun_soc(self):
        self.toma("t")
        respuesta = self.post_evaluar()
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("ningún estado de carga", respuesta.json()["detail"])

    def test_soc_reciente_actua_como_antes(self):
        self.toma("t")
        self.toma("p", categoria="prescindible")
        self.soc(20, fecha_hora=RECIENTE)
        self.assertEqual(self.evaluar(), [("p", "apagar"), ("t", "apagar")])
        self.assertEqual(len(self.alertas_bateria()), 1)

    def test_en_el_borde_de_la_ventana(self):
        self.toma("t")
        # El bloque de 10 s empieza 120 s antes: todavía cuenta.
        self.soc(20, fecha_hora=AHORA - timedelta(seconds=mediciones.VIGENCIA_S))
        self.assertEqual(self.evaluar(), [("t", "apagar")])

    def test_el_servicio_lanza_evaluacion_no_posible(self):
        self.toma("t")
        self.soc(20, fecha_hora=VIEJO)
        with self.assertRaises(EvaluacionNoPosible):
            evaluar_control_energetico(self.db, self.domo_a.id)
        # Con el reloj de la lectura (lo que hace el generador del domo demo) sí decide.
        resultado = evaluar_control_energetico(self.db, self.domo_a.id, ahora=VIEJO)
        self.assertEqual([a.accion_realizada for a in resultado.acciones], ["apagar"])

    def test_estado_no_anuncia_cortes_con_soc_viejo(self):
        self.toma("t")
        self.soc(20, fecha_hora=VIEJO)
        estado = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()
        self.assertEqual(estado["tomas_por_cortar"], [])
        # La última lectura sigue saliendo, con su fecha, para quien quiera verla.
        self.assertEqual(estado["ultima_lectura_bateria"]["estado_carga_soc"], 20)
        self.soc(20, fecha_hora=RECIENTE)
        estado = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()
        self.assertEqual([t["nombre"] for t in estado["tomas_por_cortar"]], ["t"])


class PanelConDatosViejos(ConReloj):
    def general(self):
        respuesta = self.client.get(self.ruta("/estado-general"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()

    def medidas(self):
        g = self.general()
        return (g["potencia_generada"], g["potencia_consumida"], g["estado_carga_soc"], g["estado_bateria"])

    def test_viejas_sin_dato(self):
        self.guardar("bateria", {"estado_carga_soc": 51.1, "estado_bateria": "descargando"}, VIEJO)
        self.guardar("consumo", {"potencia_consumida": 80}, VIEJO)
        self.guardar("fotovoltaica", {"potencia_generada": 300}, VIEJO)
        self.assertEqual(self.medidas(), (None, None, None, None))
        # La fecha de actualización sí dice cuándo fue el último dato.
        self.assertEqual(datetime.fromisoformat(self.general()["fecha_actualizacion"]).replace(tzinfo=timezone.utc), VIEJO)

    def test_recientes_con_valor(self):
        self.guardar("bateria", {"estado_carga_soc": 64, "estado_bateria": "cargando"}, RECIENTE)
        self.guardar("consumo", {"potencia_consumida": 45}, RECIENTE)
        self.guardar("fotovoltaica", {"potencia_generada": 210}, RECIENTE)
        self.assertEqual(self.medidas(), (210, 45, 64, "cargando"))

    def test_cada_medida_por_separado(self):
        """Un ESP32 sin sensor de batería ni PZEM: llega la generación, lo demás en null."""
        self.guardar("bateria", {"estado_carga_soc": 51.1, "estado_bateria": "descargando"}, VIEJO)
        self.guardar("consumo", {"potencia_consumida": 80}, VIEJO)
        self.guardar("bateria", {"estado_carga_soc": None}, RECIENTE)
        self.guardar("consumo", {"potencia_consumida": None}, RECIENTE)
        self.guardar("fotovoltaica", {"potencia_generada": 12}, RECIENTE)
        self.assertEqual(self.medidas(), (12, None, None, None))


class ListenerSinSocActual(ConReloj):
    def setUp(self):
        super().setUp()
        self.listener = importar_listener()
        from sqlalchemy.orm import sessionmaker

        parche = patch.object(self.listener, "SesionListener", sessionmaker(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False))
        parche.start()
        self.addCleanup(parche.stop)
        self.listener._domos_conocidos.clear()
        self.listener._ultima_evaluacion_r13.clear()
        self.listener._motivo_r13_no_evaluado.clear()
        r13 = patch.object(self.listener, "R13_AUTO", True)
        r13.start()
        self.addCleanup(r13.stop)

    def procesar_bateria(self, payload):
        self.listener._ultima_evaluacion_r13.clear()  # sin esperar los 30 s del limitador
        self.listener._procesar_mensaje(f"domo/{self.domo_a.id}/telemetria/bateria", json.dumps(payload).encode())

    def test_el_motivo_se_registra_una_sola_vez(self):
        toma = self.toma("t")
        self.soc(20, fecha_hora=VIEJO)
        with self.assertLogs("mqtt_listener", "INFO") as registro:
            for _ in range(3):
                self.procesar_bateria({"estado_carga_soc": None, "voltaje": None, "fecha_hora": RECIENTE.isoformat()})
        avisos = [linea for linea in registro.output if "no decide" in linea]
        self.assertEqual(len(avisos), 1, registro.output)
        self.assertTrue(avisos[0].startswith("WARNING"))
        self.assertEqual(self.recargar(toma).estado, "encendido")

    def test_con_soc_actual_vuelve_a_decidir(self):
        self.toma("t")
        self.soc(20, fecha_hora=VIEJO)
        self.procesar_bateria({"estado_carga_soc": None, "fecha_hora": RECIENTE.isoformat()})
        with self.assertLogs("mqtt_listener", "INFO") as registro:
            self.procesar_bateria({"estado_carga_soc": 20, "fecha_hora": AHORA.isoformat()})
        self.assertTrue(any("vuelve a haber SoC actual" in linea for linea in registro.output), registro.output)
        self.db.expire_all()
        self.assertEqual(self.db.query(Dispositivo).one().estado, "apagado")
