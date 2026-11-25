"""Recalibración guiada del SoC: POST/GET /domos/{id}/soc-recalibracion y el estado en memoria
(servicios/estado_soc.py) que llega por domo/{id}/estado/soc."""

import json
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
import mqtt_publisher
from servicios import estado_soc
from tests.base import PruebaApi, importar_listener

AHORA = datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)


def mensaje(**campos) -> bytes:
    return json.dumps(campos).encode()


class EstadoEnMemoria(TestCase):
    def setUp(self):
        estado_soc.limpiar()
        self.addCleanup(estado_soc.limpiar)

    def test_sin_sesion(self):
        self.assertEqual(estado_soc.estado_de(1, AHORA), {"estado": "sin_sesion"})

    def test_guarda_el_ultimo_por_domo(self):
        t = int((AHORA - timedelta(seconds=20)).timestamp())
        self.assertTrue(estado_soc.registrar("domo/1/estado/soc", mensaje(
            estado="lista", minutos_ok=15, requeridos=15, motivo="en reposo",
            soc_actual=100.0, soc_propuesto=58.0, voltaje=12.41, t=t)))
        estado_soc.registrar("domo/2/estado/soc", mensaje(estado="esperando", minutos_ok=1.5, requeridos=15, t=t))
        e = estado_soc.estado_de(1, AHORA)
        self.assertEqual((e["estado"], e["soc_actual"], e["soc_propuesto"], e["voltaje"]), ("lista", 100.0, 58.0, 12.41))
        self.assertEqual(e["actualizado"], AHORA - timedelta(seconds=20))
        self.assertEqual(estado_soc.estado_de(2, AHORA)["minutos_ok"], 1.5)

    def test_activa_sin_noticias_es_interrumpida(self):
        viejo = int((AHORA - timedelta(seconds=estado_soc.SIN_NOTICIAS_S + 1)).timestamp())
        for estado in ("esperando", "lista"):
            estado_soc.registrar("domo/1/estado/soc", mensaje(estado=estado, t=viejo))
            self.assertEqual(estado_soc.estado_de(1, AHORA)["estado"], "interrumpida")
        # Un estado final no se interrumpe: queda como resultado.
        estado_soc.registrar("domo/1/estado/soc", mensaje(estado="aplicada", soc_propuesto=58.0, t=viejo))
        self.assertEqual(estado_soc.estado_de(1, AHORA)["estado"], "aplicada")

    def test_sin_hora_usa_la_de_llegada(self):
        estado_soc.registrar("domo/1/estado/soc", mensaje(estado="esperando", t=None), ahora=AHORA - timedelta(seconds=30))
        self.assertEqual(estado_soc.estado_de(1, AHORA)["estado"], "esperando")
        self.assertEqual(estado_soc.estado_de(1, AHORA + timedelta(seconds=90))["estado"], "interrumpida")

    def test_inactiva_al_arrancar_reemplaza_la_sesion_vieja(self):
        estado_soc.registrar("domo/1/estado/soc", mensaje(estado="lista", soc_propuesto=58.0))
        estado_soc.registrar("domo/1/estado/soc", mensaje(estado="inactiva", t=int(AHORA.timestamp())))
        e = estado_soc.estado_de(1, AHORA)
        self.assertEqual((e["estado"], e["soc_propuesto"]), ("inactiva", None))

    def test_lo_que_no_se_entiende(self):
        with self.assertLogs("estado_soc", "WARNING"):
            self.assertFalse(estado_soc.registrar("domo/1/estado/soc", b"no es json"))
        with self.assertLogs("estado_soc", "WARNING"):
            self.assertFalse(estado_soc.registrar("domo/1/estado/soc", mensaje(estado="desconocido")))
        with self.assertLogs("estado_soc", "WARNING"):
            self.assertFalse(estado_soc.registrar("domo/x/estado/soc", mensaje(estado="lista")))
        self.assertEqual(estado_soc.estado_de(1, AHORA), {"estado": "sin_sesion"})
        estado_soc.registrar("domo/1/estado/soc", mensaje(estado="lista", soc_propuesto="58", voltaje=True))
        e = estado_soc.estado_de(1, AHORA)
        self.assertEqual((e["soc_propuesto"], e["voltaje"]), (None, None))   # no números: null


class Api(PruebaApi):
    def setUp(self):
        super().setUp()
        estado_soc.limpiar()
        self.addCleanup(estado_soc.limpiar)
        self.publicar = patch.object(mqtt_publisher, "publicar_comando_soc", return_value=True).start()
        self.escuchar = patch.object(mqtt_publisher, "escuchar_estado_soc", return_value=True).start()
        self.addCleanup(patch.stopall)

    def pedir(self, accion, usuario=None, **extra):
        return self.client.post(self.ruta("/soc-recalibracion"), json={"accion": accion, **extra}, headers=self.auth(usuario))

    def consultar(self, usuario=None):
        return self.client.get(self.ruta("/soc-recalibracion"), headers=self.auth(usuario))

    def test_el_dueno_pide_las_tres_acciones(self):
        for accion in ("iniciar", "confirmar", "cancelar"):
            with self.assertLogs("soc_recalibracion", "INFO") as registro:
                r = self.pedir(accion)
            self.assertEqual(r.status_code, 202, r.text)
            self.assertEqual(r.json(), {"domo_id": self.domo_a.id, "accion": accion, "enviado": True})
            self.publicar.assert_called_with(self.domo_a.id, accion)
            self.assertIn(self.dueno.correo, registro.output[0])
            self.assertIn(accion, registro.output[0])

    def test_nunca_un_valor_de_soc(self):
        self.assertEqual(self.pedir("confirmar", soc=90).status_code, 422)
        self.assertEqual(self.pedir("fijar").status_code, 422)
        self.assertEqual(self.client.post(self.ruta("/soc-recalibracion"), json={}, headers=self.auth()).status_code, 422)
        self.publicar.assert_not_called()

    def test_administrador_403_y_domo_ajeno_404(self):
        self.assertEqual(self.pedir("iniciar", self.admin).status_code, 403)
        self.assertEqual(self.consultar(self.admin).status_code, 403)
        intruso = self.usuario("intruso@prueba.co", "dueño")
        self.domo("Domo del intruso", intruso)
        self.db.commit()
        self.assertEqual(self.pedir("iniciar", intruso).status_code, 404)
        self.assertEqual(self.consultar(intruso).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/soc-recalibracion")).status_code, 401)
        self.publicar.assert_not_called()

    def test_broker_caido_503(self):
        self.publicar.return_value = False
        r = self.pedir("iniciar")
        self.assertEqual(r.status_code, 503)
        self.assertIn("broker", r.json()["detail"])

    def test_consulta_el_ultimo_estado(self):
        self.assertEqual(self.consultar().json(), {
            "domo_id": self.domo_a.id, "estado": "sin_sesion", "minutos_ok": None, "requeridos": None, "motivo": None,
            "soc_actual": None, "soc_propuesto": None, "voltaje": None, "actualizado": None,
        })
        estado_soc.registrar(f"domo/{self.domo_a.id}/estado/soc", mensaje(
            estado="esperando", minutos_ok=3.5, requeridos=15, motivo="hay consumo en el inversor", soc_actual=100.0))
        cuerpo = self.consultar().json()
        self.assertEqual((cuerpo["estado"], cuerpo["minutos_ok"], cuerpo["motivo"]), ("esperando", 3.5, "hay consumo en el inversor"))
        self.assertTrue(cuerpo["actualizado"].endswith(("Z", "+00:00")))
        self.escuchar.assert_called()


class EstadoSocEnElListener(TestCase):
    def setUp(self):
        self.listener = importar_listener()

    def test_solo_lo_registra_en_el_log(self):
        with self.assertLogs("mqtt_listener", "INFO") as registro:
            self.listener._procesar_mensaje("domo/1/estado/soc", mensaje(estado="lista", minutos_ok=15, soc_propuesto=58.0), True)
        self.assertIn("lista", registro.output[0])
        self.assertIn("[retenido]", registro.output[0])
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.listener._procesar_mensaje("domo/1/estado/soc", b"{}")
