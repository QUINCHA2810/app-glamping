"""Presets por batería, categorías, validaciones, permisos y configuración.

Modelo v2: los umbrales y la prioridad se calculan con la categoría y la batería y el
perfil del domo; no hay modo avanzado. La configuración vive en columnas de `domos`."""

from datetime import datetime, timezone

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from servicios.categorias import preset_de
from tests.base import PruebaApi

ESPERADOS = {
    "lifepo4": {"importante": (20, 30, 30), "confort": (35, 45, 20), "prescindible": (50, 60, 10)},
    "agm": {"importante": (50, 60, 30), "confort": (65, 75, 20), "prescindible": (80, 90, 10)},
}


class Presets(PruebaApi):
    def test_formula_por_tipo_de_bateria(self):
        for tipo, categorias in ESPERADOS.items():
            for categoria, (apagado, reactivacion, prioridad) in categorias.items():
                preset = preset_de(categoria, tipo, "conservador")
                self.assertEqual(
                    (preset.umbral_apagado_soc, preset.umbral_reactivacion_soc, preset.prioridad),
                    (apagado, reactivacion, prioridad),
                    (tipo, categoria),
                )

    def test_esencial_no_tiene_umbrales_en_ninguna_bateria(self):
        for tipo in ESPERADOS:
            preset = preset_de("esencial", tipo, "conservador")
            self.assertEqual((preset.umbral_apagado_soc, preset.umbral_reactivacion_soc, preset.prioridad), (None, None, 40))

    def test_endpoint_sin_bateria_configurada_usa_lifepo4(self):
        datos = self.client.get(self.ruta("/categorias"), headers=self.auth()).json()
        self.assertEqual(datos["tipo_bateria"], "lifepo4")
        self.assertFalse(datos["bateria_configurada"])
        self.assertEqual(datos["reserva_soc"], 20)
        self.assertEqual([c["categoria"] for c in datos["categorias"]], ["esencial", "importante", "confort", "prescindible"])
        self.assertFalse(datos["categorias"][0]["corte_automatico"])
        self.assertEqual(datos["categorias"][2]["umbral_apagado_soc"], 35)

    def test_endpoint_con_agm(self):
        self.poner_bateria("agm")
        datos = self.client.get(self.ruta("/categorias"), headers=self.auth()).json()
        self.assertEqual((datos["tipo_bateria"], datos["bateria_configurada"], datos["reserva_soc"]), ("agm", True, 50))
        por_categoria = {c["categoria"]: c for c in datos["categorias"]}
        self.assertEqual(por_categoria["prescindible"]["umbral_reactivacion_soc"], 90)

    def test_endpoint_acceso(self):
        ajeno = self.usuario("otro@prueba.co", "dueño")
        self.db.commit()
        self.assertEqual(self.client.get(self.ruta("/categorias"), headers=self.auth(self.admin)).status_code, 200)
        self.assertEqual(self.client.get(self.ruta("/categorias"), headers=self.auth(ajeno)).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/categorias")).status_code, 401)


class CrearDispositivo(PruebaApi):
    def test_toma_por_defecto_es_confort_con_su_preset(self):
        datos = self.crear({"tipo": "toma", "nombre": "Nevera", "ubicacion": "Cocina"}).json()
        self.assertEqual(datos["categoria"], "confort")
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"]), (35, 45, 20))
        self.assertEqual(datos["ubicacion"], "Cocina")
        self.assertTrue(datos["activo"])

    def test_preset_segun_la_bateria_del_domo(self):
        self.poner_bateria("agm")
        datos = self.crear({"tipo": "toma", "nombre": "T", "categoria": "importante"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"]), (50, 60, 30))

    def test_bombillo_es_esencial_y_no_admite_otra(self):
        datos = self.crear({"tipo": "bombillo", "nombre": "B"}).json()
        self.assertEqual((datos["categoria"], datos["prioridad"], datos["umbral_apagado_soc"]), ("esencial", 40, None))
        self.assertEqual(self.crear({"tipo": "bombillo", "nombre": "B", "categoria": "confort"}).status_code, 422)

    def test_sin_modo_avanzado_y_toma_esencial(self):
        avanzado = self.crear(
            {"tipo": "toma", "nombre": "T", "categoria": "confort", "umbral_apagado_soc": 22, "umbral_reactivacion_soc": 27}
        )
        self.assertEqual(avanzado.status_code, 422)
        esencial = self.crear({"tipo": "toma", "nombre": "T", "categoria": "esencial"}).json()
        self.assertEqual((esencial["umbral_apagado_soc"], esencial["prioridad"]), (None, 40))
        rechazada = self.crear(
            {"tipo": "toma", "nombre": "T", "categoria": "esencial", "umbral_apagado_soc": 22, "umbral_reactivacion_soc": 27}
        )
        self.assertEqual(rechazada.status_code, 422)

    def test_cuerpos_invalidos(self):
        toma = {"tipo": "toma", "nombre": "T"}
        for cuerpo in (
            {"tipo": "toma", "nombre": ""},
            {"tipo": "toma", "nombre": "   "},
            {"tipo": "toma"},
            {**toma, "ubicacion": "x" * 101},
            {**toma, "categoria": "inventada"},
            {**toma, "categoria": None},
            {**toma, "prioridad": 5},
            {**toma, "umbral_apagado_soc": None},
            {**toma, "umbral_apagado_soc": 30},
            {**toma, "umbral_apagado_soc": 30, "umbral_reactivacion_soc": 34},
            {**toma, "umbral_apagado_soc": -1, "umbral_reactivacion_soc": 40},
            {**toma, "umbral_apagado_soc": 30, "umbral_reactivacion_soc": 101},
            {"tipo": "bombillo", "nombre": "B", "brillo": 101},
            {"tipo": "bombillo", "nombre": "B", "umbral_apagado_soc": 30, "umbral_reactivacion_soc": 40},
        ):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.crear(cuerpo).status_code, 422)


class EditarDispositivo(PruebaApi):
    def test_cambiar_categoria_cambia_los_umbrales_calculados(self):
        toma = self.toma()
        datos = self.patch_dispositivo(toma, {"categoria": "prescindible"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"]), (50, 60, 10))

    def test_tras_cambiar_de_bateria_no_hace_falta_reenviar_la_categoria(self):
        toma = self.toma()
        self.poner_bateria("agm")
        datos = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]), (65, 75))

    def test_a_esencial_borra_umbrales_y_de_vuelta_los_recupera(self):
        toma = self.toma()
        datos = self.patch_dispositivo(toma, {"categoria": "esencial"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"]), (None, None, 40))
        datos = self.patch_dispositivo(toma, {"categoria": "importante"}).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"]), (20, 30, 30))

    def test_umbrales_junto_con_categoria_dan_422_y_no_cambian_la_categoria(self):
        toma = self.toma()
        self.assertEqual(self.patch_dispositivo(toma, {"categoria": "importante", "umbral_reactivacion_soc": 44}).status_code, 422)
        self.assertEqual(self.recargar(toma).categoria, "confort")

    def test_umbrales_en_esencial_dan_422(self):
        toma = self.toma(categoria="esencial")
        cuerpo = {"umbral_apagado_soc": 20, "umbral_reactivacion_soc": 30}
        self.assertEqual(self.patch_dispositivo(toma, cuerpo).status_code, 422)
        otra = self.toma()
        self.assertEqual(self.patch_dispositivo(otra, {**cuerpo, "categoria": "esencial"}).status_code, 422)

    def test_bombillo_solo_admite_esencial(self):
        bombillo = self.bombillo()
        self.assertEqual(self.patch_dispositivo(bombillo, {"categoria": "confort"}).status_code, 422)
        self.assertEqual(self.patch_dispositivo(bombillo, {"categoria": "esencial"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(bombillo, {"umbral_apagado_soc": 10}).status_code, 422)

    def test_nombre_y_ubicacion(self):
        toma = self.toma()
        datos = self.patch_dispositivo(toma, {"nombre": "  Nevera ", "ubicacion": "Cocina"}).json()
        self.assertEqual((datos["nombre"], datos["ubicacion"]), ("Nevera", "Cocina"))
        self.assertIsNone(self.patch_dispositivo(toma, {"ubicacion": None}).json()["ubicacion"])
        self.assertEqual(self.patch_dispositivo(toma, {"nombre": "   "}).status_code, 422)
        self.assertEqual(self.patch_dispositivo(toma, {"ubicacion": "x" * 101}).status_code, 422)

    def test_id_fisico_unico_por_domo(self):
        primera, segunda = self.toma("a"), self.toma("b")
        self.assertEqual(self.patch_dispositivo(primera, {"id_fisico": "TUYA-1"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(segunda, {"id_fisico": "TUYA-1"}).status_code, 409)
        self.assertEqual(self.patch_dispositivo(primera, {"id_fisico": "TUYA-1"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(segunda, {"id_fisico": " "}).status_code, 422)
        self.assertIsNone(self.patch_dispositivo(primera, {"id_fisico": None}).json()["id_fisico"])
        self.assertEqual(self.patch_dispositivo(segunda, {"id_fisico": "TUYA-1"}).status_code, 200)

    def test_id_fisico_se_puede_repetir_en_otro_domo(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        de_a = self.toma("a", id_fisico="TUYA-1")
        de_b = self.toma("b", domo_id=otro.id)
        respuesta = self.client.patch(
            self.ruta(f"/dispositivos/{de_b.id}", otro), json={"id_fisico": "TUYA-1"}, headers=self.auth()
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.recargar(de_a).id_fisico, "TUYA-1")

    def test_prioridad_ya_no_se_recibe(self):
        self.assertEqual(self.patch_dispositivo(self.toma(), {"prioridad": 1}).status_code, 422)


class SobreLaReserva(PruebaApi):
    """v1 validaba que un umbral propio no quedara bajo la reserva. En v2 no hay
    umbrales propios: lo que queda es que ningún preset baje de la reserva y que una
    toma se pueda seguir operando tras cambiar de batería."""

    def test_todos_los_presets_quedan_sobre_la_reserva(self):
        for tipo, reserva in (("lifepo4", 20), ("agm", 50)):
            self.poner_bateria(tipo)
            for categoria in ("importante", "confort", "prescindible"):
                with self.subTest(tipo=tipo, categoria=categoria):
                    datos = self.crear({"tipo": "toma", "nombre": "T", "categoria": categoria})
                    self.assertEqual(datos.status_code, 201)
                    self.assertGreaterEqual(datos.json()["umbral_apagado_soc"], reserva)

    def test_tras_cambiar_de_bateria_la_toma_se_opera_y_se_edita(self):
        toma = self.toma()
        self.poner_bateria("agm")
        self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(toma, {"nombre": "Otro"}).status_code, 200)
        self.assertEqual(self.patch_dispositivo(toma, {"categoria": "importante"}).status_code, 200)


class NombreDelProducto(PruebaApi):
    def test_titulo_de_la_api(self):
        self.assertEqual(self.client.get("/openapi.json").json()["info"]["title"], "Vatia API")


class NulosYRangos(PruebaApi):
    def test_nulls_en_patch_dan_422(self):
        toma = self.toma()
        for campo in ("estado", "nombre", "categoria", "activo", "umbral_apagado_soc", "umbral_reactivacion_soc"):
            with self.subTest(campo=campo):
                self.assertEqual(self.patch_dispositivo(toma, {campo: None}).status_code, 422)
        self.assertEqual((self.recargar(toma).categoria, self.recargar(toma).estado), ("confort", "encendido"))

    def test_nulls_en_configuracion_dan_422(self):
        for campo in ("umbral_soc_bajo", "umbral_soc_recuperacion", "bateria"):
            with self.subTest(campo=campo):
                respuesta = self.client.put(self.ruta("/configuracion"), json={campo: None}, headers=self.auth())
                self.assertEqual(respuesta.status_code, 422)

    def test_rangos(self):
        toma = self.toma()
        for cuerpo in (
            {"umbral_apagado_soc": -1},
            {"umbral_apagado_soc": 101},
            {"umbral_reactivacion_soc": 100.5},
            {"umbral_apagado_soc": 150, "umbral_reactivacion_soc": 200},
        ):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch_dispositivo(toma, cuerpo).status_code, 422)
        self.assertEqual(self.patch_dispositivo(self.bombillo(), {"brillo": 101}).status_code, 422)
        for cuerpo in ({"umbral_soc_bajo": -1}, {"umbral_soc_recuperacion": 101}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.client.put(self.ruta("/configuracion"), json=cuerpo, headers=self.auth()).status_code, 422)


class Permisos(PruebaApi):
    def test_administrador_enciende_apaga_y_maneja_la_iluminacion(self):
        toma, bombillo = self.toma(), self.bombillo()
        self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}, self.admin).status_code, 200)
        self.assertEqual(self.patch_dispositivo(bombillo, {"estado": "encendido"}, self.admin).status_code, 200)
        self.assertEqual(self.patch_dispositivo(bombillo, {"brillo": 50}, self.admin).status_code, 200)

    def test_administrador_no_cambia_lo_que_administra_el_dueno(self):
        toma = self.toma()
        for cuerpo in (
            {"categoria": "importante"},
            {"umbral_apagado_soc": 20},
            {"umbral_reactivacion_soc": 60},
            {"nombre": "Otro"},
            {"ubicacion": "Otra"},
            {"ubicacion": None},
            {"id_fisico": "X"},
            {"activo": False},
            {"estado": "apagado", "nombre": "Otro"},
        ):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch_dispositivo(toma, cuerpo, self.admin).status_code, 403)
                self.assertEqual(self.patch_dispositivo(toma, cuerpo).status_code in (200, 409, 422), True)
                toma = self.toma()
        self.assertEqual(self.recargar(toma).estado, "encendido")

    def test_el_403_no_cambia_nada(self):
        toma = self.toma()
        self.patch_dispositivo(toma, {"estado": "apagado", "nombre": "Otro"}, self.admin)
        recargada = self.recargar(toma)
        self.assertEqual((recargada.estado, recargada.nombre), ("encendido", "toma"))

    def test_crear_y_configurar_son_del_dueno(self):
        self.assertEqual(self.crear({"tipo": "toma", "nombre": "T"}, self.admin).status_code, 403)
        cuerpo = {"bateria": {"tipo": "agm", "capacidad_wh": 1200}}
        self.assertEqual(self.client.put(self.ruta("/configuracion"), json=cuerpo, headers=self.auth(self.admin)).status_code, 403)
        self.assertEqual(self.client.get(self.ruta("/configuracion"), headers=self.auth(self.admin)).status_code, 200)

    def test_sin_acceso_al_domo_es_404(self):
        toma = self.toma()
        ajeno = self.usuario("otro@prueba.co", "dueño")
        sin_asignar = self.usuario("admin2@prueba.co", "administrador")
        self.db.commit()
        for usuario in (ajeno, sin_asignar):
            with self.subTest(usuario=usuario.correo):
                self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}, usuario).status_code, 404)
                self.assertEqual(self.client.get(self.ruta("/dispositivos"), headers=self.auth(usuario)).status_code, 404)

    def test_administrador_puede_listar_inactivos(self):
        self.toma(activo=False, desactivado_en=datetime.now(timezone.utc))
        respuesta = self.client.get(self.ruta("/dispositivos?incluir_inactivos=true"), headers=self.auth(self.admin))
        self.assertEqual(len(respuesta.json()), 1)


class Configuracion(PruebaApi):
    """v2: la batería y el perfil son columnas de `domos`."""

    def test_bateria_y_perfil_se_guardan_en_el_domo(self):
        datos = self.client.put(
            self.ruta("/configuracion"),
            json={"bateria": {"tipo": "agm", "capacidad_wh": 1200}, "perfil_control": "equilibrado"},
            headers=self.auth(),
        ).json()
        self.assertEqual((datos["bateria"], datos["perfil_control"], datos["es_default"]), ({"tipo": "agm", "capacidad_wh": 1200}, "equilibrado", False))
        # Lo que la API sigue llamando umbrales globales: el preset confort (agm, equilibrado).
        self.assertEqual((datos["umbral_soc_bajo"], datos["umbral_soc_recuperacion"]), (60, 70))
        self.db.expire_all()
        self.assertEqual(
            (self.domo_a.bateria_tipo, self.domo_a.bateria_capacidad_wh, self.domo_a.perfil), ("agm", 1200, "equilibrado")
        )

    def test_cambiar_el_perfil_no_borra_la_bateria(self):
        self.poner_bateria("lifepo4")
        self.client.put(self.ruta("/configuracion"), json={"perfil_control": "maximo_uso"}, headers=self.auth())
        datos = self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()
        self.assertEqual((datos["bateria"]["tipo"], datos["perfil_control"]), ("lifepo4", "maximo_uso"))

    def test_los_umbrales_globales_ya_no_se_configuran(self):
        for cuerpo in ({"umbral_soc_bajo": 20}, {"umbral_soc_recuperacion": 60}):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.client.put(self.ruta("/configuracion"), json=cuerpo, headers=self.auth())
                self.assertEqual(respuesta.status_code, 422)

    def test_sin_bateria_el_get_la_devuelve_vacia(self):
        datos = self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()
        self.assertIsNone(datos["bateria"])
        self.assertTrue(datos["es_default"])
        self.assertEqual(datos["perfil_control"], "conservador")

    def test_bateria_invalida(self):
        for bateria in (
            {"tipo": "plomo", "capacidad_wh": 1200},
            {"tipo": "agm", "capacidad_wh": 0},
            {"tipo": "agm", "capacidad_wh": -5},
            {"tipo": "agm"},
            {"capacidad_wh": 1200},
        ):
            with self.subTest(bateria=bateria):
                respuesta = self.client.put(self.ruta("/configuracion"), json={"bateria": bateria}, headers=self.auth())
                self.assertEqual(respuesta.status_code, 422)

    def test_sin_capacidad_la_bateria_cuenta_como_no_configurada(self):
        self.perfil("conservador", "agm")
        categorias = self.client.get(self.ruta("/categorias"), headers=self.auth()).json()
        self.assertEqual((categorias["tipo_bateria"], categorias["bateria_configurada"]), ("agm", False))
        self.assertIsNone(self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()["bateria"])
