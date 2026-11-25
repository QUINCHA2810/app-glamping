"""Perfiles de control por domo: fórmula, umbrales calculados, vista previa y permisos.

Modelo v2: el perfil es la columna `domos.perfil` y los umbrales de cada toma se
calculan con él; cambiarlo no escribe en las tomas."""

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from servicios.categorias import preset_de
from tests.base import PruebaApi

# (categoría, perfil) -> apagado sobre la reserva
SOBRE_LA_RESERVA = {
    ("importante", "conservador"): 0,
    ("confort", "conservador"): 15,
    ("prescindible", "conservador"): 30,
    ("importante", "equilibrado"): 0,
    ("confort", "equilibrado"): 10,
    ("prescindible", "equilibrado"): 20,
    ("importante", "maximo_uso"): 0,
    ("confort", "maximo_uso"): 5,
    ("prescindible", "maximo_uso"): 10,
}
RESERVAS = {"lifepo4": 20, "agm": 50}


class Formula(PruebaApi):
    def test_apagado_y_reactivacion(self):
        for tipo, reserva in RESERVAS.items():
            for (categoria, perfil), sobre in SOBRE_LA_RESERVA.items():
                with self.subTest(tipo=tipo, categoria=categoria, perfil=perfil):
                    preset = preset_de(categoria, tipo, perfil)
                    self.assertEqual(preset.umbral_apagado_soc, reserva + sobre)
                    self.assertEqual(preset.umbral_reactivacion_soc, reserva + sobre + 10)

    def test_esencial_y_prioridad_no_dependen_del_perfil(self):
        for perfil in ("conservador", "equilibrado", "maximo_uso"):
            preset = preset_de("esencial", "agm", perfil)
            self.assertEqual((preset.umbral_apagado_soc, preset.umbral_reactivacion_soc, preset.prioridad), (None, None, 40))
            self.assertEqual(preset_de("prescindible", "agm", perfil).prioridad, 10)

    def test_ningun_preset_queda_bajo_la_reserva(self):
        for tipo, reserva in RESERVAS.items():
            for (categoria, perfil) in SOBRE_LA_RESERVA:
                self.assertGreaterEqual(preset_de(categoria, tipo, perfil).umbral_apagado_soc, reserva)


class Categorias(PruebaApi):
    def get(self, usuario=None):
        return self.client.get(self.ruta("/categorias"), headers=self.auth(usuario)).json()

    def test_por_defecto_conservador(self):
        datos = self.get()
        self.assertEqual(datos["perfil"], "conservador")
        self.assertEqual({c["categoria"]: c["umbral_apagado_soc"] for c in datos["categorias"]}["confort"], 35)

    def test_devuelve_los_tres_perfiles_con_la_bateria_del_domo(self):
        self.poner_bateria("agm")
        datos = self.get()
        self.assertEqual([p["perfil"] for p in datos["perfiles"]], ["conservador", "equilibrado", "maximo_uso"])
        confort = {
            p["perfil"]: next(c for c in p["categorias"] if c["categoria"] == "confort") for p in datos["perfiles"]
        }
        self.assertEqual(confort["conservador"]["umbral_apagado_soc"], 65)
        self.assertEqual(confort["equilibrado"]["umbral_apagado_soc"], 60)
        self.assertEqual(confort["maximo_uso"]["umbral_apagado_soc"], 55)
        self.assertEqual(confort["maximo_uso"]["umbral_reactivacion_soc"], 65)

    def test_categorias_sigue_el_perfil_actual(self):
        self.client.put(self.ruta("/configuracion"), json={"perfil_control": "maximo_uso"}, headers=self.auth())
        datos = self.get()
        self.assertEqual(datos["perfil"], "maximo_uso")
        self.assertEqual({c["categoria"]: c["umbral_apagado_soc"] for c in datos["categorias"]}["prescindible"], 30)

    def test_el_administrador_puede_verlo(self):
        self.assertEqual(self.get(self.admin)["perfil"], "conservador")

    def test_un_perfil_invalido_no_se_puede_guardar(self):
        # v1 toleraba un JSON con un perfil inválido; en v2 lo impide el CHECK de domos.perfil.
        from sqlalchemy.exc import IntegrityError

        self.domo_a.perfil = "turbo"
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.get()["perfil"], "conservador")


class CambioDePerfil(PruebaApi):
    def put(self, cuerpo, usuario=None):
        return self.client.put(self.ruta("/configuracion"), json=cuerpo, headers=self.auth(usuario))

    def umbrales(self, toma, domo=None):
        datos = self.client.get(self.ruta(f"/dispositivos/{toma.id}", domo), headers=self.auth()).json()
        return datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]

    def test_cambia_los_umbrales_de_todas_con_conteos(self):
        self.poner_bateria("agm")
        importante = self.toma("i", categoria="importante")
        confort = self.toma("c")
        prescindible = self.toma("p", categoria="prescindible")
        esencial = self.toma("e", categoria="esencial")

        datos = self.put({"perfil_control": "equilibrado"}).json()

        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (3, 0))
        self.assertEqual(datos["perfil_control"], "equilibrado")
        self.assertEqual(self.umbrales(importante), (50, 60))
        self.assertEqual(self.umbrales(confort), (60, 70))
        self.assertEqual(self.umbrales(prescindible), (70, 80))
        self.assertEqual(self.umbrales(esencial), (None, None))

    def test_de_ida_y_vuelta(self):
        toma = self.toma()
        self.put({"perfil_control": "maximo_uso"})
        self.assertEqual(self.umbrales(toma), (25, 35))
        datos = self.put({"perfil_control": "conservador"}).json()
        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (1, 0))
        self.assertEqual(self.umbrales(toma), (35, 45))

    def test_el_mismo_perfil_no_recalcula(self):
        toma = self.toma()
        datos = self.put({"perfil_control": "conservador"}).json()
        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (0, 0))
        self.assertEqual(self.umbrales(toma), (35, 45))

    def test_bateria_y_perfil_a_la_vez(self):
        toma = self.toma()
        datos = self.put({"perfil_control": "maximo_uso", "bateria": {"tipo": "agm", "capacidad_wh": 1200}}).json()
        self.assertEqual(datos["tomas_recalculadas"], 1)
        self.assertEqual(self.umbrales(toma), (55, 65))

    def test_cambiar_la_bateria_respeta_el_perfil(self):
        self.put({"perfil_control": "equilibrado"})
        toma = self.toma()
        self.poner_bateria("agm")
        self.assertEqual(self.umbrales(toma), (60, 70))

    def test_cambiar_el_perfil_no_borra_la_bateria(self):
        self.poner_bateria("agm")
        self.put({"perfil_control": "maximo_uso"})
        self.db.expire_all()
        self.assertEqual((self.domo_a.bateria_tipo, self.domo_a.bateria_capacidad_wh, self.domo_a.perfil), ("agm", 1200, "maximo_uso"))

    def test_cambiar_la_bateria_no_borra_el_perfil(self):
        self.put({"perfil_control": "maximo_uso"})
        self.poner_bateria("lifepo4")
        self.assertEqual(self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()["perfil_control"], "maximo_uso")

    def test_valores_invalidos(self):
        for valor in ("turbo", "", None, 5):
            with self.subTest(valor=valor):
                self.assertEqual(self.put({"perfil_control": valor}).status_code, 422)

    def test_solo_el_dueno(self):
        self.assertEqual(self.put({"perfil_control": "maximo_uso"}, self.admin).status_code, 403)

    def test_un_put_rechazado_no_cambia_el_perfil(self):
        toma = self.toma()
        respuesta = self.put({"perfil_control": "maximo_uso", "umbral_soc_bajo": 80})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.umbrales(toma), (35, 45))
        self.db.expire_all()
        self.assertEqual(self.domo_a.perfil, "conservador")

    def test_otro_domo_no_se_toca(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        ajena = self.toma("x", domo_id=otro.id)
        self.put({"perfil_control": "maximo_uso"})
        self.assertEqual(self.umbrales(ajena, otro), (35, 45))


class PresetsEnElRestoDeLaApi(PruebaApi):
    def setUp(self):
        super().setUp()
        self.client.put(self.ruta("/configuracion"), json={"perfil_control": "equilibrado"}, headers=self.auth())

    def test_crear_usa_el_perfil(self):
        datos = self.crear({"tipo": "toma", "nombre": "T", "categoria": "prescindible"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]), (40, 50))
        self.assertFalse(datos["personalizado"])

    def test_cambiar_categoria_usa_el_perfil(self):
        toma = self.toma()
        datos = self.patch_dispositivo(toma, {"categoria": "importante"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]), (20, 30))

    def test_globales_siguen_el_confort_del_perfil(self):
        datos = self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()
        self.assertEqual((datos["umbral_soc_bajo"], datos["umbral_soc_recuperacion"]), (30, 40))

    def test_r13_usa_los_umbrales_del_perfil(self):
        self.client.put(self.ruta("/configuracion"), json={"perfil_control": "maximo_uso"}, headers=self.auth())
        self.toma("t")  # confort en maximo_uso: 25/35
        self.soc(26)
        self.assertEqual(self.evaluar(), [])
        self.soc(24)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
