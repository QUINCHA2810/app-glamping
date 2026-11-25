"""Estado de las tomas y comunicación con la ESP32 (servicios/comunicacion.py,
domo/+/estado/toma/+ y domo/+/estado/bombillo/+ en mqtt_listener.py), el campo
`confirmacion` de los dispositivos y los reintentos de R-13."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import sessionmaker

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import ACTOR_REF_R13, ACTOR_SISTEMA, ROL_DUENO, Accion, Alerta
from servicios import comunicacion
from tests.base import PruebaApi, importar_listener


def hace(segundos: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=segundos)


class BaseComunicacion(PruebaApi):
    def setUp(self):
        super().setUp()
        self.listener = importar_listener()
        parche = patch.object(
            self.listener, "SesionListener", sessionmaker(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False)
        )
        parche.start()
        self.addCleanup(parche.stop)
        self.toma1 = self.toma("Toma pared", id_fisico="toma1_s2", estado="apagado")

    def accion(self, dispositivo, accion="encender", actor=ACTOR_SISTEMA, segundos=600):
        nuevo = "encendido" if accion == "encender" else "apagado"
        self.db.add(Accion(
            dispositivo_id=dispositivo.id, accion=accion, estado_anterior="apagado" if nuevo == "encendido" else "encendido",
            estado_nuevo=nuevo, actor_tipo=actor, actor_ref=ACTOR_REF_R13 if actor == ACTOR_SISTEMA else "dueno@prueba.co",
            resultado="ok", fecha_hora=hace(segundos),
        ))
        dispositivo.estado = nuevo
        self.db.commit()

    def reporte(self, estado="on", verificado=True, error=None, id_fisico="toma1_s2", tipo="toma", retenido=False):
        payload = {"id_fisico": id_fisico, "estado": estado, "verificado": verificado}
        if error:
            payload["error"] = error
        self.listener._procesar_mensaje(f"domo/{self.domo_a.id}/estado/{tipo}/{id_fisico}", json.dumps(payload).encode(), retenido)
        return self.recargar(self.toma1)

    def alertas(self, estado="activa"):
        self.db.expire_all()
        return self.db.query(Alerta).filter(Alerta.tipo == "dispositivo_desconectado", Alerta.estado == estado).all()

    def n_acciones(self):
        return self.db.query(Accion).count()


class ReporteDeLaToma(BaseComunicacion):
    def test_verificado_on_y_off_actualizan_el_estado_sin_accion(self):
        self.accion(self.toma1, "apagar", segundos=600)
        antes = self.n_acciones()
        with self.assertLogs("servicios.comunicacion", "INFO") as log:
            t = self.reporte("on")
        self.assertEqual((t.estado, t.conectado), ("encendido", True))
        self.assertIsNotNone(t.ultima_comunicacion)
        self.assertIn("sin fila en acciones", "\n".join(log.output))
        t = self.reporte("off")
        self.assertEqual(t.estado, "apagado")
        self.assertEqual(self.n_acciones(), antes)          # sin fila en acciones

    def test_reporte_de_una_orden_anterior_no_cambia_el_estado(self):
        self.accion(self.toma1, "encender", segundos=5)      # orden de hace 5 s
        t = self.reporte("off")                              # respuesta a la orden previa
        self.assertEqual((t.estado, t.conectado), ("encendido", True))

    def test_error_no_cambia_el_estado_y_abre_una_sola_alerta(self):
        self.accion(self.toma1, "encender", segundos=30)     # caso real del 23-nov
        t = self.reporte("desconocido", verificado=False, error="sin_conexion_toma")
        self.assertEqual((t.estado, t.conectado), ("encendido", False))
        self.assertIsNotNone(t.ultima_comunicacion)
        [alerta] = self.alertas()
        self.assertEqual((alerta.nivel, alerta.id_fisico_ref, alerta.creada_por_r13), ("advertencia", "toma1_s2", False))
        self.assertIn("Toma pared", alerta.mensaje)
        self.assertIn("3 intentos", alerta.mensaje)
        self.reporte("desconocido", verificado=False, error="sin_conexion_toma")
        self.reporte("off", verificado=False, error="ip_desconocida")
        self.assertEqual(len(self.alertas()), 1)             # no se duplica
        # Responde: la alerta se cierra sola.
        t = self.reporte("on")
        self.assertTrue(t.conectado)
        self.assertEqual(self.alertas(), [])
        self.assertEqual(len(self.alertas("cerrada")), 1)
        # Otra falla después: una alerta nueva.
        self.reporte("on", verificado=False, error="sin_conexion_toma")
        self.assertEqual(len(self.alertas()), 1)

    def test_alerta_de_otra_toma_no_se_toca(self):
        otra = self.toma("Otra", id_fisico="toma2", estado="apagado")
        self.reporte("off", verificado=False, error="sin_conexion_toma", id_fisico="toma2")
        self.reporte("off")
        self.assertEqual([a.id_fisico_ref for a in self.alertas()], ["toma2"])
        self.assertFalse(self.recargar(otra).conectado)

    def test_retenido_viejo_se_ignora(self):
        self.accion(self.toma1, "apagar", segundos=600)
        with self.assertLogs("servicios.comunicacion", "INFO"):
            t = self.reporte("on", retenido=True)                          # contradice la base
        self.assertEqual((t.estado, t.conectado, t.ultima_comunicacion), ("apagado", False, None))
        t = self.reporte("off", verificado=False, error="sin_conexion_toma", retenido=True)
        self.assertEqual((t.conectado, t.ultima_comunicacion), (False, None))
        self.assertEqual(self.alertas(), [])

    def test_retenido_coherente_confirma_y_cierra_la_alerta(self):
        self.accion(self.toma1, "apagar", segundos=600)
        self.reporte("off", verificado=False, error="sin_conexion_toma")
        t = self.reporte("off", retenido=True)
        self.assertTrue(t.conectado)
        self.assertEqual(self.alertas(), [])

    def test_desactivada_desconocida_y_sin_verificado_se_ignoran(self):
        self.toma1.activo, self.toma1.desactivado_en = False, hace(60)
        self.db.commit()
        t = self.reporte("on", verificado=False, error="sin_conexion_toma")
        self.assertEqual((t.estado, t.conectado, t.ultima_comunicacion), ("apagado", False, None))
        self.assertEqual(self.alertas(), [])
        with self.assertLogs("servicios.comunicacion", "WARNING"):
            self.reporte("on", id_fisico="no_existe")
        self.toma1.activo, self.toma1.desactivado_en = True, None
        self.db.commit()
        with self.assertLogs("servicios.comunicacion", "WARNING"):
            self.listener._procesar_mensaje(f"domo/{self.domo_a.id}/estado/toma/toma1_s2", b'{"estado": "on"}')

    def test_on_message_pasa_la_marca_retain(self):
        msg = MagicMock(topic="domo/1/estado/toma/toma1_s2", payload=b"{}", retain=1)
        with patch.object(self.listener, "_cola") as cola:
            self.listener.on_message(None, None, msg)
        topic, payload, retenido, _ = cola.put.call_args.args[0]
        self.assertEqual((topic, retenido), ("domo/1/estado/toma/toma1_s2", True))


class ComunicacionDelBombillo(BaseComunicacion):
    def setUp(self):
        super().setUp()
        self.b = self.bombillo()
        self.b.id_fisico, self.b.estado, self.b.brillo, self.b.modo_luz = "bombillo1", "encendido", 40, "blanco"
        self.db.commit()

    def test_abre_y_cierra_la_alerta_sin_tocar_estado_ni_luz(self):
        self.reporte("off", verificado=False, error="sin_conexion_bombillo", id_fisico="bombillo1", tipo="bombillo")
        b = self.recargar(self.b)
        self.assertEqual((b.estado, b.brillo, b.modo_luz, b.conectado), ("encendido", 40, "blanco", False))
        [alerta] = self.alertas()
        self.assertIn("el bombillo", alerta.mensaje)
        self.reporte("off", verificado=True, id_fisico="bombillo1", tipo="bombillo")
        b = self.recargar(self.b)
        self.assertEqual((b.estado, b.conectado), ("encendido", True))   # el estado del bombillo no cambia
        self.assertEqual(self.alertas(), [])


class Confirmacion(BaseComunicacion):
    def confirmacion(self, dispositivo=None):
        r = self.client.get(self.ruta("/dispositivos"), headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        return {d["id"]: d["confirmacion"] for d in r.json()}[(dispositivo or self.toma1).id]

    def test_estados_de_la_confirmacion(self):
        simulada = self.toma("Simulada")
        self.assertIsNone(self.confirmacion())                       # ni órdenes ni respuestas
        self.assertIsNone(self.confirmacion(simulada))
        r = self.patch_dispositivo(self.toma1, {"estado": "encendido"})
        self.assertEqual(r.json()["confirmacion"], "pendiente")
        self.db.query(Accion).update({Accion.fecha_hora: hace(90)})
        self.db.commit()
        self.assertEqual(self.confirmacion(), "sin_confirmar")     # más de 60 s sin respuesta
        self.reporte("on")
        self.assertEqual(self.confirmacion(), "confirmada")
        self.reporte("on", verificado=False, error="sin_conexion_toma")
        self.assertEqual(self.confirmacion(), "sin_conexion")
        r = self.client.get(self.ruta(f"/dispositivos/{self.toma1.id}"), headers=self.auth())
        self.assertEqual(r.json()["confirmacion"], "sin_conexion")

    def test_respuesta_anterior_a_la_orden_no_la_confirma(self):
        t = self.toma1
        t.conectado, t.ultima_comunicacion = True, hace(120)
        self.db.commit()
        self.accion(t, "encender", actor=ROL_DUENO, segundos=10)
        self.assertEqual(self.confirmacion(), "pendiente")


class ReintentosR13(BaseComunicacion):
    def fallida(self, accion="apagar", actor=ACTOR_SISTEMA, hace_s=700):
        self.accion(self.toma1, accion, actor=actor, segundos=hace_s + 20)
        self.toma1.conectado, self.toma1.ultima_comunicacion = False, hace(hace_s)
        self.db.commit()

    def reintentos(self):
        return [(r.id_fisico, r.accion) for r in comunicacion.reintentos_r13(self.db, self.domo_a.id)]

    def test_solo_ordenes_de_r13_con_la_comunicacion_fallida(self):
        self.fallida("apagar")
        self.assertEqual(self.reintentos(), [("toma1_s2", "off")])
        self.toma1.conectado = True
        self.db.commit()
        self.assertEqual(self.reintentos(), [])                     # comunicación OK

    def test_orden_de_una_persona_no_se_reintenta(self):
        self.fallida("encender", actor=ROL_DUENO)
        self.assertEqual(self.reintentos(), [])

    def test_falla_reciente_espera_10_min(self):
        self.fallida("encender", hace_s=300)
        self.assertEqual(self.reintentos(), [])

    def test_el_listener_reintenta_como_mucho_cada_10_min(self):
        self.fallida("apagar")
        with patch.object(self.listener, "publicar_comando_toma") as publicar, \
             patch.object(self.listener, "_ultimo_reintento_r13", {}), \
             patch.object(self.listener.time, "monotonic", side_effect=[1000.0, 1030.0, 1601.0]):
            with self.assertLogs("mqtt_listener", "WARNING") as log:
                self.listener._reintentar_ordenes_r13(self.domo_a.id)
            self.listener._reintentar_ordenes_r13(self.domo_a.id)        # 30 s después: no
            self.listener._reintentar_ordenes_r13(self.domo_a.id)        # 601 s después: sí
        self.assertEqual(publicar.call_count, 2)
        publicar.assert_called_with(domo_id=self.domo_a.id, accion="off", id_fisico="toma1_s2", origen="r13")
        self.assertIn("REINTENTA 'Toma pared'", log.output[0])
        t = self.recargar(self.toma1)
        self.assertEqual(t.estado, "apagado")
        self.assertEqual(self.n_acciones(), 1)                      # sin filas nuevas

    def test_la_evaluacion_automatica_revisa_los_reintentos(self):
        with patch.object(self.listener, "_reintentar_ordenes_r13") as reintentar, \
             patch.object(self.listener, "_ultima_evaluacion_r13", {}), \
             patch.object(self.listener, "R13_AUTO", True):
            self.listener._evaluar_r13_automatico(self.domo_a.id)
        reintentar.assert_called_once_with(self.domo_a.id)
