"""Órdenes MQTT hacia tomas y bombillos reales, y contrato con el firmware."""

import json
import re
from pathlib import Path
from typing import get_args
from unittest import TestCase
from unittest.mock import MagicMock, patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
import mqtt_publisher
import routers.dispositivos as router_dispositivos
import servicios.control_energetico as control
from schemas import AccionRecalibracionSoc, EstadoBateriaValor, EstadoGeneracionValor, EstadoRecalibracionSoc
from servicios import estado_soc
from tests.base import PruebaApi, importar_listener

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware" / "domo_nodo"


class Publicador(TestCase):
    def setUp(self):
        self.cliente = MagicMock()
        self.cliente.publish.return_value.rc = 0
        parche = patch.object(mqtt_publisher, "_obtener_cliente", return_value=self.cliente)
        parche.start()
        self.addCleanup(parche.stop)

    def publicado(self):
        (tema, payload), opciones = self.cliente.publish.call_args
        return tema, json.loads(payload), opciones

    def test_bombillo_tema_y_cuerpo(self):
        self.assertTrue(mqtt_publisher.publicar_comando_bombillo(1, "bombillo1", "manual", accion="on", brillo=40))
        tema, cuerpo, opciones = self.publicado()
        self.assertEqual(tema, "domo/1/comando/bombillo")
        self.assertEqual(cuerpo, {"id_fisico": "bombillo1", "origen": "manual", "accion": "on", "brillo": 40})
        self.assertEqual(opciones, {"qos": 1, "retain": False})

    def test_bombillo_solo_lo_que_cambia(self):
        mqtt_publisher.publicar_comando_bombillo(3, "b", "manual", brillo=0)
        self.assertEqual(self.publicado()[:2], ("domo/3/comando/bombillo", {"id_fisico": "b", "origen": "manual", "brillo": 0}))
        mqtt_publisher.publicar_comando_bombillo(3, "b", "manual", accion="off")
        self.assertEqual(self.publicado()[1], {"id_fisico": "b", "origen": "manual", "accion": "off"})

    def test_bombillo_valores_invalidos(self):
        for campos in ({}, {"accion": "toggle"}, {"brillo": 101}, {"brillo": -1}, {"brillo": 5.5}, {"brillo": True}):
            with self.subTest(campos=campos), self.assertRaises(ValueError):
                mqtt_publisher.publicar_comando_bombillo(1, "b", "manual", **campos)
        self.cliente.publish.assert_not_called()

    def test_bombillo_modos(self):
        casos = [
            ({"modo": "blanco", "temperatura": 0, "brillo": 30}, {"brillo": 30, "modo": "blanco", "temperatura": 0}),
            ({"accion": "on", "modo": "color", "color": {"h": 240, "s": 100}, "brillo": 80},
             {"accion": "on", "brillo": 80, "modo": "color", "color": {"h": 240, "s": 100}}),
            ({"modo": "escena", "escena": "fiesta"}, {"modo": "escena", "escena": "fiesta"}),
            ({"modo": "blanco"}, {"modo": "blanco"}),
        ]
        for campos, esperado in casos:
            with self.subTest(campos=campos):
                mqtt_publisher.publicar_comando_bombillo(1, "bombillo1", "manual", **campos)
                self.assertEqual(self.publicado()[1], {"id_fisico": "bombillo1", "origen": "manual", **esperado})

    def test_bombillo_modos_invalidos(self):
        for campos in (
            {"modo": "fiesta"},
            {"modo": "blanco", "temperatura": 101},
            {"modo": "color", "color": {"h": 361, "s": 50}},
            {"modo": "color", "color": {"h": 10}},
            {"modo": "color", "color": {"h": 10, "s": 5.5}},
            {"modo": "escena", "escena": "discoteca"},
            {"temperatura": 50, "color": {"h": 0, "s": 100}},          # dos modos
            {"modo": "blanco", "color": {"h": 0, "s": 100}},           # campo de otro modo
            {"modo": "escena", "escena": "vela", "brillo": 50},
            {"accion": "off", "modo": "blanco"},
        ):
            with self.subTest(campos=campos), self.assertRaises(ValueError):
                mqtt_publisher.publicar_comando_bombillo(1, "b", "manual", **campos)
        self.cliente.publish.assert_not_called()

    def test_la_toma_no_cambia_de_contrato(self):
        mqtt_publisher.publicar_comando_toma(1, "off", "toma2", "r13")
        self.assertEqual(self.publicado()[:2], ("domo/1/comando/toma", {"accion": "off", "id_fisico": "toma2", "origen": "r13"}))

    def test_un_broker_caido_no_lanza(self):
        with patch.object(mqtt_publisher, "_obtener_cliente", side_effect=OSError("sin broker")):
            self.assertFalse(mqtt_publisher.publicar_comando_bombillo(1, "b", "manual", accion="on"))
        self.cliente.publish.return_value.rc = 4
        self.assertFalse(mqtt_publisher.publicar_comando_bombillo(1, "b", "manual", accion="on"))

    def test_comando_soc_solo_la_accion_y_nunca_retenido(self):
        for accion in ("iniciar", "confirmar", "cancelar"):
            self.assertTrue(mqtt_publisher.publicar_comando_soc(3, accion))
            tema, cuerpo, opciones = self.publicado()
            self.assertEqual((tema, cuerpo), ("domo/3/comando/soc", {"accion": accion, "origen": "app"}))
            self.assertEqual((opciones["qos"], opciones["retain"]), (1, False))
        with self.assertRaises(ValueError):
            mqtt_publisher.publicar_comando_soc(3, "fijar")

    def test_se_suscribe_al_estado_soc_en_cada_conexion(self):
        cliente = MagicMock()
        mqtt_publisher._on_connect(cliente, None, None, 0, None)
        cliente.subscribe.assert_called_once_with("domo/+/estado/soc", qos=1)
        cliente.reset_mock()
        mqtt_publisher._on_connect(cliente, None, None, 0, None)      # una reconexión: se suscribe otra vez
        cliente.subscribe.assert_called_once_with("domo/+/estado/soc", qos=1)
        cliente.reset_mock()
        mqtt_publisher._on_connect(cliente, None, None, 5, None)      # conexión fallida: nada
        cliente.subscribe.assert_not_called()


class ClienteUnicoQueRecibe(TestCase):
    """El cliente global se crea una sola vez por proceso, con su hilo de red (loop_start)
    y la recepción de domo/+/estado/soc conectada a servicios/estado_soc."""

    def setUp(self):
        estado_soc.limpiar()
        self.addCleanup(estado_soc.limpiar)
        parche = patch.object(mqtt_publisher, "_cliente", None)
        parche.start()
        self.addCleanup(parche.stop)

    def test_un_cliente_con_loop_start_y_callback_del_estado(self):
        with patch.object(mqtt_publisher.mqtt, "Client") as Cliente:
            primero = mqtt_publisher._obtener_cliente()
            segundo = mqtt_publisher._obtener_cliente()
        self.assertIs(primero, segundo)
        Cliente.assert_called_once()
        primero.loop_start.assert_called_once_with()
        primero.message_callback_add.assert_called_once_with("domo/+/estado/soc", mqtt_publisher._on_estado_soc)
        self.assertIs(primero.on_connect, mqtt_publisher._on_connect)

    def test_el_mensaje_queda_en_memoria(self):
        msg = MagicMock(topic="domo/4/estado/soc", payload=b'{"estado":"esperando","minutos_ok":2.5,"requeridos":15}')
        mqtt_publisher._on_estado_soc(None, None, msg)
        self.assertEqual(estado_soc.estado_de(4)["minutos_ok"], 2.5)

    def test_sin_broker_no_lanza(self):
        with patch.object(mqtt_publisher, "_obtener_cliente", side_effect=OSError("sin broker")):
            self.assertFalse(mqtt_publisher.escuchar_estado_soc(espera_s=0))


class OrdenesDesdeLaApi(PruebaApi):
    def setUp(self):
        super().setUp()
        self.pub_toma = router_dispositivos.publicar_comando_toma
        self.pub_bombillo = router_dispositivos.publicar_comando_bombillo

    def bombillo_real(self, id_fisico="bombillo1"):
        b = self.bombillo()
        b.id_fisico = id_fisico
        self.db.commit()
        return b

    def test_toma_real(self):
        toma = self.toma("toma2", id_fisico="toma2")
        self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}).status_code, 200)
        self.pub_toma.assert_called_once_with(domo_id=self.domo_a.id, accion="off", id_fisico="toma2", origen="manual")
        self.pub_bombillo.assert_not_called()

    def secuencia(self, b, casos):
        """Cada (cuerpo, orden esperada o None = no publica), en orden, sobre el mismo bombillo."""
        for cuerpo, esperado in casos:
            with self.subTest(cuerpo=cuerpo):
                self.pub_bombillo.reset_mock()
                self.assertEqual(self.patch_dispositivo(b, cuerpo).status_code, 200)
                if esperado is None:
                    self.pub_bombillo.assert_not_called()
                else:
                    self.pub_bombillo.assert_called_once_with(domo_id=self.domo_a.id, origen="manual", id_fisico="bombillo1", **esperado)

    def test_bombillo_real_encendido_y_brillo(self):
        b = self.bombillo_real()
        self.secuencia(b, [
            ({"estado": "encendido"}, {"accion": "on"}),
            ({"estado": "apagado"}, {"accion": "off"}),
            ({"brillo": 40}, None),                                  # apagado: solo se guarda
            ({"estado": "encendido", "brillo": 100}, {"accion": "on", "brillo": 100}),
            ({"brillo": 0}, {"brillo": 0}),
        ])
        self.pub_toma.assert_not_called()

    def test_el_administrador_tambien(self):
        b = self.bombillo_real()
        b.estado = "encendido"
        self.db.commit()
        self.assertEqual(self.patch_dispositivo(b, {"brillo": 25}, usuario=self.admin).status_code, 200)
        self.pub_bombillo.assert_called_once_with(domo_id=self.domo_a.id, origen="manual", id_fisico="bombillo1", brillo=25)

    def test_modos_con_el_bombillo_encendido(self):
        b = self.bombillo_real()
        b.estado, b.brillo = "encendido", 60
        self.db.commit()
        self.secuencia(b, [
            ({"temperatura_color": "calida"}, {"modo": "blanco", "brillo": 60, "temperatura": 0}),
            ({"modo_luz": "blanco", "temperatura_color": "fria", "brillo": 100}, {"modo": "blanco", "brillo": 100, "temperatura": 100}),
            ({"color": "#0000ff"}, {"modo": "color", "brillo": 100, "color": {"h": 240, "s": 100}}),
            # El brillo en modo color va con el color: no se pierde.
            ({"brillo": 30}, {"modo": "color", "brillo": 30, "color": {"h": 240, "s": 100}}),
            ({"escena": "fiesta"}, {"modo": "escena", "escena": "fiesta"}),
            # De vuelta a blanco, con la temperatura guardada.
            ({"modo_luz": "blanco"}, {"modo": "blanco", "brillo": 30, "temperatura": 100}),
            ({"color": "rojo"}, {"modo": "color", "brillo": 30, "color": {"h": 0, "s": 100}}),
        ])

    def test_apagado_solo_guarda_y_al_encender_va_el_modo(self):
        b = self.bombillo_real()
        self.secuencia(b, [
            ({"escena": "vela"}, None),
            ({"color": "#00ff00"}, None),
            ({"brillo": 70}, None),
            ({"estado": "encendido"}, {"accion": "on", "modo": "color", "brillo": 70, "color": {"h": 120, "s": 100}}),
            ({"estado": "apagado"}, {"accion": "off"}),
            # Lo que manda el frontend con el bombillo apagado: encender + modo en un PATCH.
            ({"estado": "encendido", "escena": "oceano"}, {"accion": "on", "modo": "escena", "escena": "oceano"}),
            ({"estado": "apagado", "modo_luz": "blanco", "temperatura_color": "neutra"}, {"accion": "off"}),
        ])
        guardado = self.recargar(b)
        self.assertEqual((guardado.modo_luz, guardado.temperatura_color, guardado.escena), ("blanco", "neutra", "oceano"))

    def test_lo_que_no_publica(self):
        simulado = self.bombillo("simulado")
        real = self.bombillo_real()
        self.patch_dispositivo(simulado, {"estado": "encendido", "brillo": 50})
        self.patch_dispositivo(simulado, {"escena": "disco"})
        # El real está apagado: la luz se guarda, pero no se publica.
        self.assertEqual(self.patch_dispositivo(real, {"color": "rojo"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(real, {"temperatura_color": "calida"}).status_code, 200)
        real.estado = "encendido"
        self.db.commit()
        self.patch_dispositivo(real, {"nombre": "Lámpara"})
        self.pub_bombillo.assert_not_called()
        self.pub_toma.assert_not_called()

    def test_un_error_de_validacion_no_publica(self):
        b = self.bombillo_real()
        self.assertEqual(self.patch_dispositivo(b, {"brillo": 101}).status_code, 422)
        self.pub_bombillo.assert_not_called()

    def test_r13_nunca_toca_los_bombillos(self):
        b = self.bombillo_real()
        b.estado = "encendido"
        toma = self.toma("toma1", id_fisico="toma1")
        toma.estado = "encendido"
        self.db.commit()
        self.soc(5.0)
        acciones = self.evaluar()
        self.assertEqual(acciones, [("toma1", "apagar")])
        self.assertEqual(self.recargar(b).estado, "encendido")
        self.pub_bombillo.assert_not_called()
        llamadas = [c.kwargs["id_fisico"] for c in control.publicar_comando_toma.call_args_list]
        self.assertEqual(llamadas, ["toma1"])
        self.assertFalse(hasattr(control, "publicar_comando_bombillo"))


class ContratoConElFirmware(TestCase):
    """Lo que publica firmware/domo_nodo coincide con lo que guarda mqtt_listener.py."""

    @classmethod
    def setUpClass(cls):
        cls.sensores = (FIRMWARE / "Sensores.ino").read_text(encoding="utf-8")
        cls.principal = (FIRMWARE / "domo_nodo.ino").read_text(encoding="utf-8")
        cls.estimador_soc = (FIRMWARE / "soc_estimador.cpp").read_text(encoding="utf-8")
        cls.recalibracion = (FIRMWARE / "soc_recalibracion.cpp").read_text(encoding="utf-8")
        cls.listener = importar_listener()

    def test_campos_de_telemetria(self):
        documentos = {"fv": "fotovoltaica", "ba": "bateria", "co": "consumo", "s": "sensores"}
        usados = {tipo: set() for tipo in documentos.values()}
        for doc, campo in re.findall(r'\b(fv|ba|co|s)\["(\w+)"\]', self.sensores):
            usados[documentos[doc]].add(campo)
        for doc, campo in re.findall(r'ponerNumero\((fv|ba|co|s), "(\w+)"', self.sensores):
            usados[documentos[doc]].add(campo)
        for tipo, campos in usados.items():
            with self.subTest(tipo=tipo):
                self.assertTrue(campos)
                guardados, aceptados = self.listener.MAPEO_TIPOS[tipo]
                self.assertLessEqual(campos, guardados | aceptados)
        self.assertIn('"domo/%d/telemetria/%s"', self.sensores)
        for tipo in ("fotovoltaica", "bateria", "consumo", "sensores"):
            self.assertIn(f'publicarJson("{tipo}"', self.sensores)

    def test_estados_dentro_de_los_catalogos(self):
        self.assertLessEqual(set(re.findall(r'"(generando|sin_generacion)"', self.sensores)), set(get_args(EstadoGeneracionValor)))
        # estado_bateria sale de soc::textoEstado (firmware/domo_nodo/soc_estimador.cpp).
        cuerpo = self.estimador_soc.split("const char* textoEstado", 1)[1].split("\n}", 1)[0]
        estados = set(re.findall(r'return "(\w+)";', cuerpo))
        self.assertEqual(estados, set(get_args(EstadoBateriaValor)))
        self.assertIn("soc::textoEstado(estadoSoc)", self.sensores)

    def test_temas_y_campos_de_las_ordenes(self):
        self.assertIn('"domo/1/comando/toma"', self.principal)
        self.assertIn('"domo/1/comando/bombillo"', self.principal)
        for campo in ("id_fisico", "accion", "origen", "brillo", "modo", "temperatura", "color", "escena"):
            self.assertIn(f'cmd["{campo}"]', self.principal)

    def test_recalibracion_del_soc_comando_y_estado(self):
        # Comando: el tema, QoS 1 y las acciones que entiende el firmware = las del backend.
        self.assertIn('"domo/1/comando/soc"', self.principal)
        self.assertIn("mqtt.subscribe(TOPIC_CMD_SOC, 1)", self.principal)
        cuerpo = self.sensores.split("bool pedirRecal", 1)[1].split("\n}", 1)[0]
        self.assertEqual(set(re.findall(r'!strcmp\(accion, "(\w+)"\)', cuerpo)), set(get_args(AccionRecalibracionSoc)))
        self.assertEqual(set(mqtt_publisher.ACCIONES_SOC), set(get_args(AccionRecalibracionSoc)))
        # El callback solo lee "accion" (nunca un valor de SoC).
        callback = self.principal.split("if (!strcmp(topic, TOPIC_CMD_SOC))", 1)[1].split("uint8_t tipo;", 1)[0]
        self.assertEqual(re.findall(r'cmd\["(\w+)"\]', callback), ["accion"])
        # Estado: el tema, RETENIDO, y los estados de soc_recalibracion.cpp = los del backend.
        publicar = self.sensores.split("bool publicarEstadoSoc", 1)[1].split("\n}", 1)[0]
        self.assertIn('"domo/%d/estado/soc"', publicar)
        self.assertIn("mqtt.publish(tema, cuerpo, true)", publicar)
        textos = self.recalibracion.split("const char* textoEstado", 1)[1].split("\n}", 1)[0]
        self.assertEqual(set(re.findall(r'return "(\w+)";', textos)), set(get_args(EstadoRecalibracionSoc)))
        self.assertIn("inactiva", get_args(EstadoRecalibracionSoc))
        for campo in ("minutos_ok", "requeridos", "motivo", "soc_actual", "soc_propuesto", "voltaje", "t"):
            self.assertIn(f'"{campo}"', publicar)
        self.assertEqual(estado_soc.TEMA_ESTADO_SOC, "domo/+/estado/soc")

    def test_campos_del_estado_del_bombillo(self):
        """Lo que publica publicarEstado es lo que lee servicios/iluminacion.campos_del_estado."""
        cuerpo = self.principal.split("void publicarEstado", 1)[1].split("\n}", 1)[0]
        for campo in ("brillo", "modo", "temperatura", "color"):
            self.assertIn(f'e["{campo}"]', cuerpo)
        self.assertIn('c["h"]', cuerpo)
        self.assertIn('c["s"]', cuerpo)
        self.assertIn('"domo/%d/estado/%s/%s"', cuerpo)


class EstadoDelBombilloEnElListener(PruebaApi):
    def setUp(self):
        super().setUp()
        self.listener = importar_listener()
        from sqlalchemy.orm import sessionmaker

        parche = patch.object(
            self.listener, "SesionListener", sessionmaker(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False)
        )
        parche.start()
        self.addCleanup(parche.stop)
        self.bombillo_real = self.bombillo()
        self.bombillo_real.id_fisico = "bombillo1"
        self.bombillo_real.escena = "vela"
        self.db.commit()

    def recibir(self, payload, id_fisico="bombillo1", domo_id=None):
        tema = f"domo/{domo_id or self.domo_a.id}/estado/bombillo/{id_fisico}"
        self.listener._procesar_mensaje(tema, json.dumps(payload).encode())
        return self.recargar(self.bombillo_real)

    def test_se_suscribe_al_estado(self):
        cliente = MagicMock()
        self.listener.on_connect(cliente, None, None, 0, None)
        temas = [tema for tema, _ in cliente.subscribe.call_args.args[0]]
        self.assertEqual(temas, ["domo/+/telemetria/+", "domo/+/estado/bombillo/+", "domo/+/estado/toma/+", "domo/+/estado/soc"])

    def test_guarda_modo_brillo_temperatura_y_color(self):
        b = self.recibir({"id_fisico": "bombillo1", "estado": "on", "verificado": True, "brillo": 45, "modo": "color",
                          "temperatura": 90, "color": {"h": 120, "s": 100}})
        self.assertEqual((b.modo_luz, b.brillo, b.temperatura_color, b.color), ("color", 45, "fria", "#00ff00"))
        self.assertEqual(b.escena, "vela")          # el estado no trae la escena: se conserva
        self.assertEqual(b.estado, "apagado")       # ni toca el encendido

    def test_firmware_viejo_no_borra_nada(self):
        self.bombillo_real.modo_luz, self.bombillo_real.color = "color", "#ff0000"
        self.db.commit()
        b = self.recibir({"id_fisico": "bombillo1", "estado": "on", "verificado": True, "brillo": 20})
        self.assertEqual((b.modo_luz, b.brillo, b.color), ("color", 20, "#ff0000"))

    def test_lo_que_se_ignora(self):
        antes = (self.bombillo_real.modo_luz, self.bombillo_real.brillo)
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.recibir({"id_fisico": "otro", "estado": "desconocido", "verificado": False, "error": "id_fisico_desconocido"}, "otro")
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.recibir({"brillo": 10, "modo": "blanco"}, id_fisico="no_existe")
        toma = self.toma("toma", id_fisico="toma1")
        with self.assertLogs("mqtt_listener", "WARNING"):
            self.recibir({"brillo": 10}, id_fisico="toma1")      # una toma con ese id no es un bombillo
        with self.assertLogs("mqtt_listener", "ERROR"):
            self.listener._procesar_mensaje(f"domo/{self.domo_a.id}/estado/bombillo/bombillo1", b"no es json")
        b = self.recargar(self.bombillo_real)
        self.assertEqual((b.modo_luz, b.brillo), antes)
        self.assertIsNone(self.recargar(toma).brillo)

    def test_otro_domo_no_se_toca(self):
        otro = self.domo("Domo B", self.dueno)
        with self.assertLogs("mqtt_listener", "WARNING"):
            b = self.recibir({"brillo": 99, "modo": "blanco"}, domo_id=otro.id)
        self.assertIsNone(b.brillo)
