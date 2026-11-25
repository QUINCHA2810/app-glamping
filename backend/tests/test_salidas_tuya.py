"""Ronda F2: cada salida de un equipo Tuya es una carga aparte en Vatia.

La toma de pared de 3 salidas es toma1 (DPS 1), toma1_s2 (DPS 2) y toma1_usb (DPS 3)
en el firmware. Para el backend son tres dispositivos con su propio id_fisico y su
categoría, y R-13 los corta por separado, el menos importante primero. No hace falta
migración: id_fisico es texto libre, único por domo.
"""

import importlib.util
import json
import re
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import MagicMock, patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
import mqtt_publisher
import routers.dispositivos as router_dispositivos
import servicios.control_energetico as control
from tests.base import PruebaApi

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware" / "domo_nodo"
IDS_DEL_PROTOTIPO = ("toma1", "toma1_s2", "toma1_usb", "toma2", "bombillo1")
# El firmware publica en domo/{id}/estado/{tipo}/{id_fisico}: el id no puede romper el tema.
ID_SEGURO = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# IPs reales del prototipo (reservadas en el router; ronda F2b).
IPS_DEL_PROTOTIPO = {
    "bombillo1": "192, 168, 0, 160",
    "toma2": "192, 168, 0, 161",
    "toma1": "192, 168, 0, 162",
    "toma1_s2": "192, 168, 0, 162",
    "toma1_usb": "192, 168, 0, 162",
}
# tinytuya NO es dependencia del backend (no está en requirements): solo hace falta para
# regenerar o comprobar los vectores del firmware.
HAY_TINYTUYA = importlib.util.find_spec("tinytuya") is not None


def entradas_del_ejemplo():
    """Las líneas de DISPOSITIVOS_TUYA en secrets.h.example (nunca se lee secrets.h)."""
    texto = (FIRMWARE / "secrets.h.example").read_text(encoding="utf-8")
    patron = re.compile(
        r'\{\s*"(?P<id>[^"]*)",\s*(?P<tipo>TIPO_\w+),\s*"(?P<tuya>[^"]*)",\s*\{\s*(?P<ip>[\d,\s]+)\},\s*'
        r'"(?P<clave>[^"]*)",\s*(?P<version>\d+),\s*(?P<dps>\d+)\s*\}'
    )
    return [m.groupdict() for m in patron.finditer(texto.split("#define DISPOSITIVOS_TUYA", 1)[1])]


class SalidasComoCargasEnLaApi(PruebaApi):
    def test_la_api_acepta_los_ids_de_las_salidas(self):
        for id_fisico, categoria in (("toma1_s2", "confort"), ("toma1_usb", "prescindible")):
            with self.subTest(id_fisico=id_fisico):
                r = self.crear({"tipo": "toma", "nombre": id_fisico, "categoria": categoria, "id_fisico": id_fisico})
                self.assertEqual(r.status_code, 201, r.text)
                self.assertEqual(r.json()["id_fisico"], id_fisico)

    def test_cada_salida_recibe_su_propia_orden(self):
        salidas = [self.toma(i, id_fisico=i) for i in ("toma1", "toma1_s2", "toma1_usb")]
        pub = router_dispositivos.publicar_comando_toma
        for toma in salidas:
            pub.reset_mock()
            self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}).status_code, 200)
            pub.assert_called_once_with(domo_id=self.domo_a.id, accion="off", id_fisico=toma.id_fisico, origen="manual")

    def test_r13_corta_primero_la_salida_menos_importante(self):
        # Mismo equipo físico, tres cargas: USB prescindible, salida 2 de confort, salida 1 importante.
        self.toma("Salida 1", categoria="importante", id_fisico="toma1")
        self.toma("Salida 2", categoria="confort", id_fisico="toma1_s2")
        self.toma("USB", categoria="prescindible", id_fisico="toma1_usb")

        self.soc(40.0)
        self.assertEqual(self.evaluar(), [("USB", "apagar")])
        ordenes = [c.kwargs for c in control.publicar_comando_toma.call_args_list]
        self.assertEqual(ordenes, [{"domo_id": self.domo_a.id, "accion": "off", "id_fisico": "toma1_usb", "origen": "r13"}])

    def test_r13_manda_una_orden_por_salida_aunque_sean_del_mismo_equipo(self):
        # Dos órdenes seguidas al mismo equipo físico: el backend publica las dos; el
        # firmware las ejecuta en serie (ver firmware/README.md, "Órdenes").
        self.toma("Salida 1", categoria="importante", id_fisico="toma1")
        self.toma("Salida 2", categoria="confort", id_fisico="toma1_s2")
        self.toma("USB", categoria="prescindible", id_fisico="toma1_usb")

        self.soc(30.0)
        self.assertEqual(self.evaluar(), [("USB", "apagar"), ("Salida 2", "apagar")])
        ids = [c.kwargs["id_fisico"] for c in control.publicar_comando_toma.call_args_list]
        self.assertEqual(ids, ["toma1_usb", "toma1_s2"])

    def test_el_tema_y_el_cuerpo_de_la_orden_no_cambian(self):
        cliente = MagicMock()
        cliente.publish.return_value.rc = 0
        with patch.object(mqtt_publisher, "_obtener_cliente", return_value=cliente):
            self.assertTrue(mqtt_publisher.publicar_comando_toma(1, "off", "toma1_usb", "r13"))
        (tema, payload), opciones = cliente.publish.call_args
        self.assertEqual(tema, "domo/1/comando/toma")
        self.assertEqual(json.loads(payload), {"accion": "off", "id_fisico": "toma1_usb", "origen": "r13"})
        self.assertEqual(opciones, {"qos": 1, "retain": False})


class ContratoDeLasSalidasConElFirmware(TestCase):
    """secrets.h.example y el firmware tratan cada salida como una entrada con su DPS."""

    @classmethod
    def setUpClass(cls):
        cls.entradas = entradas_del_ejemplo()
        cls.principal = (FIRMWARE / "domo_nodo.ino").read_text(encoding="utf-8")
        cls.config = (FIRMWARE / "dispositivos_config.h").read_text(encoding="utf-8")

    def test_el_ejemplo_tiene_las_cinco_cargas_del_prototipo(self):
        self.assertEqual(tuple(e["id"] for e in self.entradas), IDS_DEL_PROTOTIPO)

    def test_ids_validos_para_el_tema_mqtt_y_unicos(self):
        ids = [e["id"] for e in self.entradas]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, ID_SEGURO)
        # El firmware rechaza al arrancar un id que no cumpla lo mismo (idSeguro).
        self.assertIn("isalnum((unsigned char)id[k]) && id[k] != '_' && id[k] != '-'", self.principal)
        self.assertIn("n > 32", self.principal)

    def test_salidas_del_mismo_equipo_comparten_conexion_y_no_el_dps(self):
        por_equipo = {}
        for e in self.entradas:
            por_equipo.setdefault(e["tuya"], []).append(e)
        toma1 = por_equipo["ID_TUYA_TOMA1"]
        self.assertEqual([e["id"] for e in toma1], ["toma1", "toma1_s2", "toma1_usb"])
        self.assertEqual([e["dps"] for e in toma1], ["1", "2", "3"])
        for grupo in por_equipo.values():
            self.assertEqual(len({(e["ip"], e["clave"], e["version"]) for e in grupo}), 1)
            self.assertEqual(len({e["dps"] for e in grupo}), len(grupo))

    def test_ips_del_prototipo(self):
        self.assertEqual({e["id"]: " ".join(e["ip"].split()).rstrip(" ,") for e in self.entradas}, IPS_DEL_PROTOTIPO)
        readme = (FIRMWARE.parent / "README.md").read_text(encoding="utf-8")
        for id_fisico, ip in IPS_DEL_PROTOTIPO.items():
            fila = next(l for l in readme.splitlines() if l.startswith(f"| `{id_fisico}` |"))
            self.assertIn(ip.replace(", ", "."), fila)

    def test_versiones_y_dps_del_prototipo(self):
        por_id = {e["id"]: e for e in self.entradas}
        self.assertEqual(por_id["toma1"]["version"], "33")
        self.assertEqual(por_id["toma2"]["version"], "34")
        self.assertEqual(por_id["bombillo1"]["version"], "35")
        self.assertEqual((por_id["bombillo1"]["tipo"], por_id["bombillo1"]["dps"]), ("TIPO_BOMBILLO", "20"))
        self.assertIn("#define DPS_TOMA_POR_DEFECTO        1", self.config)
        self.assertIn("#define DPS_BOMBILLO_POR_DEFECTO   20", self.config)

    def test_el_ejemplo_no_trae_claves_reales(self):
        for e in self.entradas:
            self.assertEqual(len(e["clave"]), 16)
            self.assertTrue(e["clave"].startswith("LOCAL_KEY_"), e["clave"])
            self.assertTrue(e["tuya"].startswith("ID_TUYA_"), e["tuya"])

    def test_el_estado_se_publica_por_id_fisico(self):
        self.assertIn('"domo/%d/estado/%s/%s", DOMO_ID, nombreTipo(d.tipo), d.idFisico', self.principal)


@skipUnless(HAY_TINYTUYA, "tinytuya no está instalado (no es dependencia del backend): esta prueba solo "
                          "compara firmware/domo_nodo/tuya_vectores.h con lo que genera tinytuya. "
                          "Instálalo aparte para correrla.")
class VectoresDelProtocoloTuya(TestCase):
    """firmware/domo_nodo/tuya_vectores.h sigue siendo lo que produce tinytuya.

    Esos vectores son los que corre el comando "autotest" en la ESP32 (y
    firmware/pruebas_tuya/prueba_pc.cpp en el PC). Si se actualiza tinytuya o el
    generador y cambian, esta prueba avisa: hay que regenerarlos y volver a probar
    en el PC y en la placa.
    """

    def test_el_archivo_coincide_con_tinytuya(self):
        ruta = FIRMWARE.parent / "pruebas_tuya" / "generar_vectores.py"
        spec = importlib.util.spec_from_file_location("generar_vectores", ruta)
        generador = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generador)
        actual = (FIRMWARE / "tuya_vectores.h").read_text(encoding="utf-8")
        self.assertEqual(generador.generar(), actual,
                         "tuya_vectores.h está desactualizado: corre firmware/pruebas_tuya/generar_vectores.py")
