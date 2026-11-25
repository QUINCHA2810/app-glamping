"""Luz del bombillo (servicios/iluminacion.py): valores válidos, modos, escenas, presets,
conversiones hex <-> HSV, el estado que publica el ESP32 y GET /iluminacion/opciones.
Las órdenes MQTT que salen de un PATCH están en tests/test_comandos_mqtt.py."""

import importlib.util
import re
from pathlib import Path
from typing import get_args
from unittest import TestCase

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Dispositivo
from schemas import ModoLuzValor
from servicios import iluminacion
from servicios.iluminacion import (
    COLORES,
    ESCENAS,
    MODOS_LUZ,
    PRESETS,
    TEMPERATURAS_COLOR,
    TONOS_COLOR,
    VALORES_COLOR,
    VALORES_ESCENA,
    VALORES_TEMPERATURA_COLOR,
    campos_del_estado,
    color_a_hs,
    hex_a_hs,
    hs_a_hex,
    orden_de_luz,
    temperatura_mas_cercana,
)
from tests.base import PruebaApi

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_FIRMWARE = RAIZ / "firmware" / "domo_nodo" / "dispositivos_config.h"
MIGRACION = RAIZ / "backend" / "alembic_v2" / "versions" / "9d4f2a7b1c38_dispositivos_modo_luz_y_escena.py"


class ListasUnicas(TestCase):
    def test_las_escenas_coinciden_con_el_firmware(self):
        """Si cambia la tabla ESCENAS_BOMBILLO del firmware (o la del backend), falla."""
        texto = CONFIG_FIRMWARE.read_text(encoding="utf-8")
        tabla = texto.split("ESCENAS_BOMBILLO[] = {", 1)[1].split("};", 1)[0]
        del_firmware = re.findall(r'\{\s*"(\w+)",\s*"[0-9a-f]+"\s*\}', tabla)
        self.assertEqual(del_firmware, ["fiesta", "arcoiris", "romantico", "disco", "estrobo", "vela", "atardecer", "oceano"])
        self.assertEqual(list(VALORES_ESCENA), del_firmware)

    def test_cada_escena_tiene_nombre_en_espanol(self):
        self.assertEqual(len({etiqueta for _, etiqueta in ESCENAS}), len(ESCENAS))
        self.assertTrue(all(etiqueta and etiqueta[0].isupper() for _, etiqueta in ESCENAS))

    def test_modos_iguales_en_esquema_y_migracion(self):
        self.assertEqual(tuple(get_args(ModoLuzValor)), MODOS_LUZ)
        spec = importlib.util.spec_from_file_location("migracion_luz", MIGRACION)
        migracion = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migracion)
        self.assertEqual(migracion.MODOS_LUZ, MODOS_LUZ)
        self.assertEqual(migracion.down_revision, "4e8b2c6d9f17")

    def test_cada_nombre_de_color_tiene_tono(self):
        self.assertEqual(set(TONOS_COLOR), set(VALORES_COLOR))

    def test_el_firmware_acepta_los_campos_del_contrato(self):
        principal = (RAIZ / "firmware" / "domo_nodo" / "domo_nodo.ino").read_text(encoding="utf-8")
        for campo in ("modo", "temperatura", "color", "escena"):
            self.assertIn(f'cmd["{campo}"]', principal)
        for modo in MODOS_LUZ:
            self.assertIn(f'"{modo}"', principal)


class Presets(TestCase):
    def test_los_seis_presets(self):
        self.assertEqual(
            [(p["valor"], p["temperatura_color"], p["brillo"]) for p in PRESETS],
            [
                ("calido", "calida", None),
                ("neutro", "neutra", None),
                ("frio", "fria", None),
                ("lectura", "fria", 100),
                ("relajacion", "calida", 30),
                ("noche", "calida", 10),
            ],
        )

    def test_la_temperatura_de_cada_preset_es_valida(self):
        for preset in PRESETS:
            self.assertIn(preset["temperatura_color"], VALORES_TEMPERATURA_COLOR)


class Conversiones(TestCase):
    def test_hex_a_hs(self):
        for hex_, esperado in (("#ff0000", (0, 100)), ("#00ff00", (120, 100)), ("#0000ff", (240, 100)),
                               ("#ffffff", (0, 0)), ("#ff8080", (0, 50)), ("#000000", (0, 0))):
            with self.subTest(hex_=hex_):
                self.assertEqual(hex_a_hs(hex_), esperado)

    def test_ida_y_vuelta(self):
        for h in range(0, 360, 15):
            for s in (0, 25, 50, 75, 100):
                h2, s2 = hex_a_hs(hs_a_hex(h, s))
                with self.subTest(h=h, s=s):
                    self.assertLessEqual(abs(s2 - s), 1)
                    if s >= 25:
                        self.assertLessEqual(min(abs(h2 - h), 360 - abs(h2 - h)), 2)

    def test_hs_a_hex_va_al_brillo_maximo(self):
        self.assertEqual(hs_a_hex(0, 100), "#ff0000")
        self.assertEqual(hs_a_hex(0, 0), "#ffffff")
        self.assertEqual(hs_a_hex(360, 100), "#ff0000")

    def test_color_guardado_a_hs(self):
        self.assertEqual(color_a_hs("rojo"), (0, 100))
        self.assertEqual(color_a_hs(" Azul "), (240, 100))
        self.assertEqual(color_a_hs("#00FF00"), (120, 100))
        for invalido in (None, "string", "#ff00", "ff0000"):
            self.assertIsNone(color_a_hs(invalido))

    def test_temperatura_mas_cercana(self):
        for valor, esperado in ((0, "calida"), (24, "calida"), (26, "neutra"), (50, "neutra"), (76, "fria"), (100, "fria")):
            with self.subTest(valor=valor):
                self.assertEqual(temperatura_mas_cercana(valor), esperado)


class OrdenDeLuz(TestCase):
    def test_cada_modo(self):
        self.assertEqual(orden_de_luz("blanco", 30, "#ff0000", "calida", "fiesta"), {"modo": "blanco", "brillo": 30, "temperatura": 0})
        self.assertEqual(orden_de_luz("color", 80, "#00ff00", "fria", "fiesta"), {"modo": "color", "brillo": 80, "color": {"h": 120, "s": 100}})
        self.assertEqual(orden_de_luz("escena", 80, "#00ff00", "fria", "vela"), {"modo": "escena", "escena": "vela"})
        self.assertEqual(orden_de_luz(None, 40, "rojo", "fria", None), {"brillo": 40})
        self.assertEqual(orden_de_luz(None, None, None, None, None), {})

    def test_un_valor_viejo_no_se_manda(self):
        self.assertEqual(orden_de_luz("blanco", None, None, "string", None), {"modo": "blanco"})
        self.assertEqual(orden_de_luz("color", None, "string", None, None), {"modo": "color"})


class EstadoDelBombillo(TestCase):
    def test_estado_completo(self):
        estado = {"id_fisico": "bombillo1", "estado": "on", "verificado": True, "brillo": 30, "modo": "color",
                  "temperatura": 60, "color": {"h": 240, "s": 100}}
        self.assertEqual(campos_del_estado(estado), {"modo_luz": "color", "brillo": 30, "temperatura_color": "neutra", "color": "#0000ff"})

    def test_firmware_viejo_solo_lo_que_viene(self):
        self.assertEqual(campos_del_estado({"id_fisico": "b", "estado": "on", "verificado": True, "brillo": 55}), {"brillo": 55})
        self.assertEqual(campos_del_estado({"id_fisico": "b", "estado": "desconocido", "verificado": False}), {})

    def test_valores_raros_se_ignoran(self):
        estado = {"modo": "otro", "brillo": 101, "temperatura": True, "color": {"h": 400, "s": 50}}
        self.assertEqual(campos_del_estado(estado), {})
        self.assertEqual(campos_del_estado({"color": "rojo", "brillo": "50"}), {})


class Validacion(PruebaApi):
    def test_valores_validos_se_aceptan(self):
        bombillo = self.bombillo()
        respuesta = self.patch_dispositivo(bombillo, {"temperatura_color": "calida"})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual((respuesta.json()["temperatura_color"], respuesta.json()["modo_luz"]), ("calida", "blanco"))
        respuesta = self.patch_dispositivo(bombillo, {"color": "rojo"})
        self.assertEqual((respuesta.json()["color"], respuesta.json()["modo_luz"]), ("rojo", "color"))
        # La temperatura se conserva al pasar a color: vuelve con el modo blanco.
        self.assertEqual(respuesta.json()["temperatura_color"], "calida")

    def test_color_hex(self):
        respuesta = self.patch_dispositivo(self.bombillo(), {"color": " #FF8800 "})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json()["color"], "#ff8800")

    def test_se_normalizan_espacios_y_mayusculas(self):
        bombillo = self.bombillo()
        self.assertEqual(self.patch_dispositivo(bombillo, {"color": " Azul "}).json()["color"], "azul")
        self.assertEqual(self.patch_dispositivo(bombillo, {"temperatura_color": "FRIA"}).json()["temperatura_color"], "fria")
        self.assertEqual(self.patch_dispositivo(bombillo, {"escena": " Vela "}).json()["escena"], "vela")

    def test_el_administrador_tambien(self):
        respuesta = self.patch_dispositivo(self.bombillo(), {"escena": "fiesta"}, self.admin)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)

    def test_null_lo_borra(self):
        bombillo = self.bombillo()
        self.patch_dispositivo(bombillo, {"color": "rojo"})
        respuesta = self.patch_dispositivo(bombillo, {"color": None})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.json()["color"])

    def test_valor_invalido_da_422_con_las_opciones(self):
        bombillo = self.bombillo()
        for cuerpo, campo in (
            ({"color": "string"}, "Color"),
            ({"color": "#ff00"}, "Color"),
            ({"temperatura_color": "string"}, "Temperatura de color"),
            ({"temperatura_color": "4000K"}, "Temperatura de color"),
            ({"escena": "discoteca"}, "Escena"),
        ):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.patch_dispositivo(bombillo, cuerpo)
                self.assertEqual(respuesta.status_code, 422)
                mensaje = respuesta.json()["detail"][0]["msg"]
                self.assertTrue(mensaje.startswith(f"{campo}: Debe ser uno de: "), mensaje)
        self.assertIsNone(self.recargar(bombillo).color)

    def test_no_se_mezclan_modos(self):
        bombillo = self.bombillo()
        for cuerpo in (
            {"color": "rojo", "temperatura_color": "calida"},
            {"escena": "vela", "color": "#ff0000"},
            {"modo_luz": "blanco", "color": "rojo"},
            {"modo_luz": "escena", "temperatura_color": "fria"},
            {"modo_luz": "fiesta"},
        ):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch_dispositivo(bombillo, cuerpo).status_code, 422)
        self.assertIsNone(self.recargar(bombillo).modo_luz)

    def test_modo_escena_necesita_escena(self):
        bombillo = self.bombillo()
        respuesta = self.patch_dispositivo(bombillo, {"modo_luz": "escena"})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(respuesta.json()["detail"], "El modo escena necesita una escena.")
        self.patch_dispositivo(bombillo, {"escena": "oceano"})
        self.patch_dispositivo(bombillo, {"modo_luz": "blanco"})
        # La escena guardada sirve para volver al modo escena.
        self.assertEqual(self.patch_dispositivo(bombillo, {"modo_luz": "escena"}).status_code, 200)

    def test_un_preset_es_un_solo_patch(self):
        bombillo = self.bombillo()
        respuesta = self.patch_dispositivo(bombillo, {"modo_luz": "blanco", "temperatura_color": "fria", "brillo": 100})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        datos = respuesta.json()
        self.assertEqual((datos["modo_luz"], datos["temperatura_color"], datos["brillo"]), ("blanco", "fria", 100))

    def test_al_crear_tambien(self):
        respuesta = self.crear({"tipo": "bombillo", "nombre": "b", "color": "fucsia"})
        self.assertEqual(respuesta.status_code, 422)
        respuesta = self.crear({"tipo": "bombillo", "nombre": "b", "color": "#ff00ff", "temperatura_color": "neutra"})
        self.assertEqual(respuesta.status_code, 201, respuesta.text)

    def test_en_una_toma_sigue_siendo_422(self):
        toma = self.toma()
        for cuerpo in ({"color": "rojo"}, {"modo_luz": "blanco"}, {"escena": "vela"}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch_dispositivo(toma, cuerpo).status_code, 422)

    def test_la_base_tambien_lo_impide(self):
        from sqlalchemy.exc import IntegrityError

        for campos in ({"tipo": "toma", "modo_luz": "blanco"}, {"tipo": "toma", "escena": "vela"},
                       {"tipo": "bombillo", "modo_luz": "fiesta"}):
            with self.subTest(campos=campos):
                self.db.add(Dispositivo(**{"domo_id": self.domo_a.id, "nombre": "x", "categoria": "esencial", **campos}))
                with self.assertRaises(IntegrityError):
                    self.db.commit()
                self.db.rollback()


class DatosViejos(PruebaApi):
    def test_leer_un_valor_viejo_invalido_no_falla(self):
        bombillo = Dispositivo(
            domo_id=self.domo_a.id, tipo="bombillo", nombre="b", categoria="esencial", color="string", temperatura_color="string"
        )
        self.db.add(bombillo)
        self.db.commit()
        uno = self.client.get(self.ruta(f"/dispositivos/{bombillo.id}"), headers=self.auth())
        self.assertEqual(uno.status_code, 200, uno.text)
        self.assertEqual((uno.json()["color"], uno.json()["temperatura_color"], uno.json()["modo_luz"]), ("string", "string", None))
        lista = self.client.get(self.ruta("/dispositivos"), headers=self.auth())
        self.assertEqual(lista.status_code, 200, lista.text)

    def test_cambiar_otro_campo_no_choca_con_el_valor_viejo(self):
        bombillo = Dispositivo(domo_id=self.domo_a.id, tipo="bombillo", nombre="b", categoria="esencial", temperatura_color="string")
        self.db.add(bombillo)
        self.db.commit()
        respuesta = self.patch_dispositivo(bombillo, {"brillo": 40, "color": "rojo"})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json()["temperatura_color"], "string")


class Opciones(PruebaApi):
    def test_entrega_las_listas_del_backend(self):
        respuesta = self.client.get("/iluminacion/opciones", headers=self.auth(self.admin))
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual([(o["valor"], o["etiqueta"]) for o in datos["colores"]], list(COLORES))
        self.assertEqual(next(o["hex"] for o in datos["colores"] if o["valor"] == "rojo"), "#ff0000")
        self.assertEqual([(o["valor"], o["etiqueta"]) for o in datos["temperaturas_color"]], list(TEMPERATURAS_COLOR))
        self.assertEqual([(o["valor"], o["etiqueta"]) for o in datos["escenas"]], list(ESCENAS))
        self.assertEqual(datos["presets"], [dict(p) for p in PRESETS])
        self.assertTrue(datos["se_envian_al_bombillo"])
        self.assertIs(iluminacion.SE_ENVIAN_AL_BOMBILLO, True)

    def test_exige_sesion(self):
        self.assertEqual(self.client.get("/iluminacion/opciones").status_code, 401)
