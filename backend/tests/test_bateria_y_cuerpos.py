"""Cambio de batería, campo personalizado, nivel de alerta, y cuerpos con null o
números no finitos en toda la API."""

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from main import app
from tests.base import PruebaApi


class CambioDeBateria(PruebaApi):
    """Modelo v2: los umbrales se calculan, así que cambiar la batería cambia los de
    todas las tomas al instante, sin escribir en ellas. `tomas_recalculadas` cuenta
    las tomas no esenciales del domo y `tomas_personalizadas` es siempre 0."""

    def put_bateria(self, tipo: str, capacidad: float = 1200):
        respuesta = self.client.put(
            self.ruta("/configuracion"), json={"bateria": {"tipo": tipo, "capacidad_wh": capacidad}}, headers=self.auth()
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()

    def umbrales(self, dispositivo, domo=None):
        datos = self.client.get(self.ruta(f"/dispositivos/{dispositivo.id}", domo), headers=self.auth()).json()
        return datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]

    def test_cambia_los_umbrales_calculados_de_todas(self):
        importante = self.toma("i", categoria="importante")
        confort = self.toma("c")
        prescindible = self.toma("p", categoria="prescindible")
        esencial = self.toma("e", categoria="esencial")

        datos = self.put_bateria("agm")

        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (3, 0))
        self.assertEqual(self.umbrales(importante), (50, 60))
        self.assertEqual(self.umbrales(confort), (65, 75))
        self.assertEqual(self.umbrales(prescindible), (80, 90))
        self.assertEqual(self.umbrales(esencial), (None, None))

    def test_de_ida_y_vuelta(self):
        toma = self.toma()
        self.put_bateria("agm")
        self.assertEqual(self.umbrales(toma), (65, 75))
        datos = self.put_bateria("lifepo4")
        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (1, 0))
        self.assertEqual(self.umbrales(toma), (35, 45))

    def test_mismo_tipo_o_solo_otra_capacidad_no_recalcula(self):
        toma = self.toma()
        for capacidad in (1200, 2400):
            with self.subTest(capacidad=capacidad):
                datos = self.put_bateria("lifepo4", capacidad)
                self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (0, 0))
        self.assertEqual(self.umbrales(toma), (35, 45))
        self.db.expire_all()
        self.assertEqual(self.domo_a.bateria_capacidad_wh, 2400)

    def test_sin_bateria_previa_la_anterior_es_lifepo4(self):
        toma = self.toma()
        antes = self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()
        self.assertTrue(antes["es_default"])
        self.assertIsNone(antes["bateria"])
        self.assertEqual(self.put_bateria("agm")["tomas_recalculadas"], 1)
        self.assertEqual(self.umbrales(toma), (65, 75))

    def test_inactivas_tambien_y_otros_domos_no(self):
        from datetime import datetime, timezone

        self.toma("i", activo=False, desactivado_en=datetime.now(timezone.utc))
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        ajena = self.toma("a", domo_id=otro.id)
        self.assertEqual(self.put_bateria("agm")["tomas_recalculadas"], 1)
        self.assertEqual(self.umbrales(ajena, otro), (35, 45))

    def test_prioridad_y_categoria_no_cambian(self):
        toma = self.toma()
        self.put_bateria("agm")
        datos = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertEqual((datos["categoria"], datos["prioridad"]), ("confort", 20))

    def test_put_sin_cambios_informa_cero_y_get_no_informa(self):
        self.toma()
        datos = self.client.put(self.ruta("/configuracion"), json={"perfil_control": "conservador"}, headers=self.auth()).json()
        self.assertEqual((datos["tomas_recalculadas"], datos["tomas_personalizadas"]), (0, 0))
        datos = self.client.get(self.ruta("/configuracion"), headers=self.auth()).json()
        self.assertIsNone(datos["tomas_recalculadas"])

    def test_un_put_rechazado_no_cambia_nada(self):
        toma = self.toma()
        for extra in ({"umbral_soc_bajo": 80}, {"limites_alerta": "x"}, {"parametros_control": "x"}):
            with self.subTest(extra=extra):
                respuesta = self.client.put(
                    self.ruta("/configuracion"),
                    json={"bateria": {"tipo": "agm", "capacidad_wh": 1200}, **extra},
                    headers=self.auth(),
                )
                self.assertEqual(respuesta.status_code, 422)
                self.assertIn("ya no se", respuesta.json()["detail"])
        self.assertEqual(self.umbrales(toma), (35, 45))

    def test_r13_usa_los_umbrales_de_la_bateria_nueva(self):
        self.toma("t")
        self.soc(60)
        self.assertEqual(self.evaluar(), [])
        self.put_bateria("agm")
        self.assertEqual(self.evaluar(), [("t", "apagar")])

    def test_el_administrador_no_puede_cambiarla(self):
        respuesta = self.client.put(
            self.ruta("/configuracion"), json={"perfil_control": "maximo_uso"}, headers=self.auth(self.admin)
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.client.get(self.ruta("/configuracion"), headers=self.auth(self.admin)).status_code, 200)


class Personalizado(PruebaApi):
    """v2: no hay umbrales propios, así que ninguna toma es personalizada."""

    def get(self, dispositivo):
        return self.client.get(self.ruta(f"/dispositivos/{dispositivo.id}"), headers=self.auth()).json()

    def test_siempre_es_false(self):
        self.assertFalse(self.crear({"tipo": "toma", "nombre": "T"}).json()["personalizado"])
        self.assertFalse(self.crear({"tipo": "bombillo", "nombre": "B"}).json()["personalizado"])
        self.assertFalse(self.crear({"tipo": "toma", "nombre": "E", "categoria": "esencial"}).json()["personalizado"])
        toma = self.toma()
        self.poner_bateria("agm")
        self.assertFalse(self.get(toma)["personalizado"])

    def test_el_administrador_tambien_lo_ve(self):
        toma = self.toma()
        respuesta = self.patch_dispositivo(toma, {"estado": "apagado"}, self.admin)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertFalse(respuesta.json()["personalizado"])


class NivelDeAlerta(PruebaApi):
    def nivel_con(self, soc):
        self.toma("t")  # confort: lifepo4 35, agm 65
        self.soc(soc)
        self.evaluar()
        return self.alertas_bateria()[0].nivel_prioridad

    def test_lifepo4_justo_en_la_reserva_es_critico(self):
        self.assertEqual(self.nivel_con(20), "critico")

    def test_lifepo4_sobre_la_reserva_es_advertencia(self):
        self.assertEqual(self.nivel_con(20.1), "advertencia")

    def test_sin_bateria_configurada_usa_lifepo4(self):
        # Con el 15 % fijo de antes, 18 habría sido advertencia.
        self.assertEqual(self.nivel_con(18), "critico")

    def test_agm_justo_en_la_reserva_es_critico(self):
        self.poner_bateria("agm")
        self.assertEqual(self.nivel_con(50), "critico")

    def test_agm_sobre_la_reserva_es_advertencia(self):
        self.poner_bateria("agm")
        self.assertEqual(self.nivel_con(50.1), "advertencia")


NO_FINITOS = ("NaN", "Infinity", "-Infinity", "1e999", "-1e999")


class NumerosNoFinitos(PruebaApi):
    def rutas_con_cuerpo(self):
        for ruta, operaciones in app.openapi()["paths"].items():
            for metodo, operacion in operaciones.items():
                if "requestBody" in operacion:
                    yield metodo, ruta.replace("{domo_id}", str(self.domo_a.id)).replace("{dispositivo_id}", "1").replace(
                        "{administrador_id}", "1"
                    ).replace("{alerta_id}", "1")

    def test_en_todos_los_endpoints_con_cuerpo(self):
        rutas = list(self.rutas_con_cuerpo())
        self.assertGreaterEqual(len(rutas), 15)
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        for metodo, ruta in rutas:
            for valor in NO_FINITOS:
                with self.subTest(metodo=metodo, ruta=ruta, valor=valor):
                    respuesta = self.client.request(metodo, ruta, content=f'{{"x": {valor}}}', headers=cabeceras)
                    self.assertEqual(respuesta.status_code, 422, respuesta.text)
                    self.assertIn("NaN", respuesta.json()["detail"][0]["msg"])

    def test_anidado_en_listas_y_objetos(self):
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        for cuerpo in ('{"bateria": {"tipo": "agm", "capacidad_wh": NaN}}', '{"a": [1, [2, {"b": Infinity}]]}'):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.client.put(self.ruta("/configuracion"), content=cuerpo, headers=cabeceras)
                self.assertEqual(respuesta.status_code, 422)

    def test_tipo_json_con_parametros_o_sin_content_type(self):
        for cabecera in ({"Content-Type": "application/json; charset=utf-8"}, {"Content-Type": "application/vnd.x+json"}, {}):
            with self.subTest(cabecera=cabecera):
                respuesta = self.client.put(
                    self.ruta("/configuracion"), content='{"umbral_soc_bajo": NaN}', headers={**self.auth(), **cabecera}
                )
                self.assertEqual(respuesta.status_code, 422)

    def test_no_guarda_nada(self):
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        toma = self.toma()
        respuesta = self.client.patch(self.ruta(f"/dispositivos/{toma.id}"), content='{"nombre": NaN}', headers=cabeceras)
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.recargar(toma).nombre, "toma")
        self.client.post(self.ruta("/lecturas-bateria"), content='{"voltaje": Infinity}', headers=cabeceras)
        lecturas = self.client.get(self.ruta("/lecturas-bateria"), headers=self.auth()).json()
        self.assertEqual(lecturas, [])

    def test_lleva_cabeceras_cors(self):
        respuesta = self.client.put(
            self.ruta("/configuracion"),
            content='{"umbral_soc_bajo": NaN}',
            headers={"Content-Type": "application/json", "Origin": "http://localhost:5173"},
        )
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(respuesta.headers.get("access-control-allow-origin"), "http://localhost:5173")

    def test_como_texto_en_cualquier_float(self):
        for ruta, campo in (
            ("/lecturas-bateria", "voltaje"),
            ("/lecturas-fotovoltaicas", "corriente"),
            ("/lecturas-consumo", "potencia_consumida"),
            ("/lecturas-sensores", "valor"),
        ):
            for valor in ("NaN", "Infinity", "-inf"):
                with self.subTest(ruta=ruta, valor=valor):
                    respuesta = self.client.post(self.ruta(ruta), json={campo: valor}, headers=self.auth())
                    self.assertEqual(respuesta.status_code, 422, respuesta.text)

    def test_entero_enorme_no_da_500(self):
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        respuesta = self.client.post(self.ruta("/lecturas-bateria"), content='{"voltaje": 1' + "0" * 400 + "}", headers=cabeceras)
        self.assertEqual(respuesta.status_code, 422, respuesta.text)

    def test_lo_valido_sigue_funcionando(self):
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        respuesta = self.client.put(
            self.ruta("/configuracion"), content='{"bateria": {"tipo": "agm", "capacidad_wh": 1200.5}}', headers=cabeceras
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json()["bateria"]["capacidad_wh"], 1200.5)
        # Un texto "NaN" en un campo de texto no es un número: llega tal cual.
        respuesta = self.client.post(
            self.ruta("/alertas"),
            json={"tipo_alerta": "sobrecarga", "nivel_prioridad": "critico", "descripcion": "NaN"},
            headers=self.auth(),
        )
        self.assertEqual((respuesta.status_code, respuesta.json()["descripcion"]), (201, "NaN"))

    def test_json_mal_formado_lo_sigue_informando_fastapi(self):
        cabeceras = {**self.auth(), "Content-Type": "application/json"}
        respuesta = self.client.put(self.ruta("/configuracion"), content="{no es json", headers=cabeceras)
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(respuesta.json()["detail"][0]["type"], "json_invalid")


class NulosEnToda(PruebaApi):
    def test_put_domo(self):
        respuesta = self.client.put(self.ruta(), json={"nombre": None}, headers=self.auth())
        self.assertEqual(respuesta.status_code, 422)
        self.db.expire_all()
        self.assertEqual(self.domo_a.nombre, "Domo A")
        # v2 ya no guarda descripcion ni estado_domo: se ignoran.
        for campo in ("ubicacion", "descripcion", "estado_domo"):
            with self.subTest(campo=campo):
                self.assertEqual(self.client.put(self.ruta(), json={campo: None}, headers=self.auth()).status_code, 200)

    def test_put_perfil(self):
        self.assertEqual(self.client.put("/users/me", json={"nombre": None}, headers=self.auth()).status_code, 422)
        self.assertEqual(self.client.put("/users/me", json={"telefono": None}, headers=self.auth()).status_code, 200)

    def test_ningun_campo_de_ningun_cuerpo_da_500_con_null(self):
        """Barrido: cuerpo válido de cada endpoint, poniendo null en cada propiedad
        que declara su esquema, incluidas las que el cuerpo válido no trae."""
        esquemas = app.openapi()["components"]["schemas"]
        toma = self.toma()
        alerta = self.client.post(
            self.ruta("/alertas"),
            json={"tipo_alerta": "sobrecarga", "nivel_prioridad": "critico", "descripcion": "x"},
            headers=self.auth(),
        ).json()
        d = self.domo_a.id
        casos = {
            ("post", "/auth/login"): {"correo": "dueno@prueba.co", "contrasena": "x"},
            ("post", "/auth/registro"): {"nombre": "n", "correo": "nuevo@prueba.co", "contrasena": "12345678", "domo_id": d},
            ("put", "/users/me"): {},
            ("post", "/users/me/contrasena"): {"contrasena_actual": "x", "contrasena_nueva": "clave-nueva-1"},
            ("post", f"/administradores/{self.admin.id}/restablecer-contrasena"): {"contrasena_temporal": "clave-nueva-1"},
            ("patch", f"/administradores/{self.admin.id}"): {},
            ("post", "/domos"): {"nombre": "n"},
            ("put", f"/domos/{d}"): {},
            ("post", f"/domos/{d}/administradores"): {"correo": "admin@prueba.co"},
            ("patch", f"/domos/{d}/administradores/{self.admin.id}"): {},
            ("post", f"/domos/{d}/dispositivos"): {"tipo": "toma", "nombre": "n"},
            ("patch", f"/domos/{d}/dispositivos/{toma.id}"): {},
            ("post", f"/domos/{d}/lecturas-fotovoltaicas"): {},
            ("post", f"/domos/{d}/lecturas-consumo"): {},
            ("post", f"/domos/{d}/lecturas-bateria"): {},
            ("post", f"/domos/{d}/lecturas-sensores"): {},
            ("post", f"/domos/{d}/alertas"): {},
            ("patch", f"/domos/{d}/alertas/{alerta['id']}"): {"estado_alerta": "atendida"},
            ("post", f"/domos/{d}/dispositivos/{toma.id}/estado-comunicacion"): {},
            ("put", f"/domos/{d}/configuracion"): {},
            ("post", f"/domos/{d}/soc-recalibracion"): {"accion": "cancelar"},
        }
        por_plantilla = {}
        for ruta, operaciones in app.openapi()["paths"].items():
            for metodo, operacion in operaciones.items():
                if "requestBody" in operacion:
                    referencia = operacion["requestBody"]["content"]["application/json"]["schema"]["$ref"]
                    por_plantilla[(metodo, ruta)] = esquemas[referencia.rsplit("/", 1)[1]]
        self.assertEqual(len(por_plantilla), len(casos), "Hay un endpoint con cuerpo sin caso en este barrido.")

        def esquema_de(metodo, ruta):
            for (m, plantilla), esquema in por_plantilla.items():
                partes, reales = plantilla.split("/"), ruta.split("/")
                if m == metodo and len(partes) == len(reales) and all(
                    p == r or p.startswith("{") for p, r in zip(partes, reales)
                ):
                    return esquema
            self.fail(f"Sin esquema: {metodo} {ruta}")

        for (metodo, ruta), cuerpo in casos.items():
            propiedades = esquema_de(metodo, ruta)["properties"]
            for campo in propiedades:
                with self.subTest(metodo=metodo, ruta=ruta, campo=campo):
                    respuesta = self.client.request(metodo, ruta, json={**cuerpo, campo: None}, headers=self.auth())
                    self.assertLess(respuesta.status_code, 500, respuesta.text)
