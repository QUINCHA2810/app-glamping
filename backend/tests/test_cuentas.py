"""Contraseñas, cierre de sesiones, restricción de asignaciones y mensajes en español."""

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Usuario
from tests.test_administradores import CLAVE, Base


class Restablecer(Base):
    def setUp(self):
        super().setUp()
        self.admin_id = self.crear_admin().json()["id"]
        self.token = {"Authorization": f"Bearer {self.login('nuevo@prueba.co').json()['access_token']}"}

    def restablecer(self, admin_id=None, contrasena="temporal-123", usuario=None):
        return self.client.post(
            f"/administradores/{admin_id or self.admin_id}/restablecer-contrasena",
            json={"contrasena_temporal": contrasena},
            headers=self.auth(usuario),
        )

    def test_cambia_la_contrasena_y_cierra_sesiones(self):
        self.assertEqual(self.client.get("/domos", headers=self.token).status_code, 200)
        respuesta = self.restablecer()
        self.assertEqual(respuesta.status_code, 204)
        self.assertEqual(respuesta.content, b"")
        self.assertEqual(self.client.get("/users/me", headers=self.token).status_code, 401)
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 401)
        self.assertEqual(self.login("nuevo@prueba.co", "temporal-123").status_code, 200)

    def test_misma_validacion(self):
        for contrasena in ("corta", "x" * 73, "ñ" * 37):
            with self.subTest(contrasena=contrasena[:5]):
                self.assertEqual(self.restablecer(contrasena=contrasena).status_code, 422)
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 200)

    def test_solo_las_cuentas_propias(self):
        ajeno = self.usuario("ajeno@prueba.co", "dueño")
        domo_ajeno = self.domo("Domo ajeno", ajeno)
        self.db.commit()
        suyo = self.crear_admin("suyo@prueba.co", usuario=ajeno, domo=domo_ajeno).json()["id"]
        for admin_id in (suyo, self.dueno.id, ajeno.id, 9999):
            with self.subTest(admin_id=admin_id):
                self.assertEqual(self.restablecer(admin_id).status_code, 404)
        self.assertEqual(self.login("suyo@prueba.co").status_code, 200)
        # v2: self.admin solo atiende domos de este dueño, así que es cuenta propia
        # aunque no la creara por la API (en v1 daba 404 por no tener creado_por_id).
        self.assertEqual(self.restablecer(self.admin.id).status_code, 204)

    def test_el_administrador_no_puede(self):
        self.assertEqual(self.restablecer(usuario=self.admin).status_code, 403)
        self.assertEqual(self.client.post(f"/administradores/{self.admin_id}/restablecer-contrasena", json={"contrasena_temporal": "temporal-123"}).status_code, 401)

    def test_no_se_guarda_en_texto_plano(self):
        self.restablecer()
        self.db.expire_all()
        self.assertNotIn("temporal-123", self.db.get(Usuario, self.admin_id).contrasena_hash)


class CambioPropio(Base):
    def setUp(self):
        super().setUp()
        self.crear_admin()
        self.token = {"Authorization": f"Bearer {self.login('nuevo@prueba.co').json()['access_token']}"}

    def cambiar(self, actual=CLAVE, nueva="clave-nueva-22", cabeceras=None):
        return self.client.post("/users/me/contrasena", json={"contrasena_actual": actual, "contrasena_nueva": nueva}, headers=cabeceras or self.token)

    def test_cambia_y_devuelve_un_token_valido_sin_datos_de_mas(self):
        respuesta = self.cambiar()
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(set(respuesta.json()), {"access_token", "token_type"})
        nuevo = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}
        self.assertEqual(self.client.get("/users/me", headers=nuevo).status_code, 200)
        self.assertEqual(self.login("nuevo@prueba.co", "clave-nueva-22").status_code, 200)
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 401)

    def test_cierra_las_demas_sesiones(self):
        self.cambiar()
        self.assertEqual(self.client.get("/users/me", headers=self.token).status_code, 401)

    def test_actual_incorrecta_da_400_y_no_cierra_la_sesion(self):
        respuesta = self.cambiar(actual="otra-cosa-1")
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.client.get("/users/me", headers=self.token).status_code, 200)
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 200)

    def test_validaciones(self):
        self.assertEqual(self.cambiar(nueva="corta").status_code, 422)
        self.assertEqual(self.cambiar(nueva=CLAVE).status_code, 422)
        self.assertEqual(self.cambiar(nueva="x" * 73).status_code, 422)

    def test_el_dueno_tambien_puede(self):
        dueno = self.usuario("d2@prueba.co", "dueño")
        from security import hash_password

        dueno.contrasena_hash = hash_password(CLAVE)
        self.db.commit()
        self.assertEqual(self.cambiar(cabeceras=self.auth(dueno)).status_code, 200)

    def test_sin_sesion(self):
        self.assertEqual(self.client.post("/users/me/contrasena", json={"contrasena_actual": "x", "contrasena_nueva": "yyyyyyyy"}).status_code, 401)

    def test_token_de_antes_de_la_columna_vale_como_version_0(self):
        import jwt

        from security import ALGORITHM, SECRET_KEY

        viejo = jwt.encode({"sub": str(self.dueno.id), "rol": "dueño", "exp": 9999999999}, SECRET_KEY, algorithm=ALGORITHM)
        self.assertEqual(self.client.get("/users/me", headers={"Authorization": f"Bearer {viejo}"}).status_code, 200)


class RestriccionDeAsignacion(Base):
    def setUp(self):
        super().setUp()
        self.ajeno = self.usuario("ajeno@prueba.co", "dueño")
        self.domo_ajeno = self.domo("Domo ajeno", self.ajeno)
        self.db.commit()

    def test_creado_por_el_en_otro_de_sus_domos(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        self.crear_admin()
        self.assertEqual(self.asignar_domo("nuevo@prueba.co", otro).status_code, 201)

    def test_con_un_domo_suyo_asignado_aunque_no_lo_creara(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        self.assertEqual(self.asignar_domo("admin@prueba.co", otro).status_code, 201)

    def test_con_un_domo_quitado_y_otro_vigente_se_puede_reasignar(self):
        # v2: quitar es borrar la fila; se puede reasignar mientras le quede otro domo mío.
        otro = self.domo("Domo B", self.dueno)
        self.asignar(self.admin, otro)
        self.db.commit()
        quitar = self.client.patch(self.ruta(f"/administradores/{self.admin.id}"), json={"estado_asignacion": "inactivo"}, headers=self.auth())
        self.assertEqual(quitar.status_code, 200)
        self.assertEqual(self.asignar_domo("admin@prueba.co").status_code, 201)

    def test_de_otro_dueno_da_404_igual_que_uno_que_no_existe(self):
        self.crear_admin("suyo@prueba.co", usuario=self.ajeno, domo=self.domo_ajeno)
        cuenta_ajena = self.asignar_domo("suyo@prueba.co")
        inexistente = self.asignar_domo("nadie@prueba.co")
        self.assertEqual((cuenta_ajena.status_code, inexistente.status_code), (404, 404))
        self.assertEqual(cuenta_ajena.json(), inexistente.json())

    def test_administrador_sin_domos_da_404(self):
        self.usuario("suelto@prueba.co", "administrador")
        self.db.commit()
        self.assertEqual(self.asignar_domo("suelto@prueba.co").status_code, 404)

    def test_un_dueno_no_se_puede_asignar_y_no_revela_que_existe(self):
        self.assertEqual(self.asignar_domo("ajeno@prueba.co").status_code, 404)
        self.assertEqual(self.asignar_domo("dueno@prueba.co").status_code, 404)

    def test_el_otro_dueno_no_puede_con_los_de_este(self):
        self.crear_admin()
        self.assertEqual(self.asignar_domo("nuevo@prueba.co", self.domo_ajeno, self.ajeno).status_code, 404)
        self.assertEqual(self.asignar_domo("admin@prueba.co", self.domo_ajeno, self.ajeno).status_code, 404)


class MensajesEnEspanol(Base):
    def mensajes(self, respuesta):
        self.assertEqual(respuesta.status_code, 422, respuesta.text)
        return [d["msg"] for d in respuesta.json()["detail"]]

    def registro(self, **campos):
        cuerpo = {"nombre": "N", "correo": "a@prueba.co", "contrasena": CLAVE, "domo_id": self.domo_a.id, **campos}
        return self.client.post("/auth/registro", json=cuerpo, headers=self.auth())

    def test_correo_invalido(self):
        for correo in ("no-es-correo", "a@test.local", "a@"):
            with self.subTest(correo=correo):
                self.assertEqual(self.mensajes(self.registro(correo=correo)), ["Correo: No es un correo válido."])

    def test_longitud(self):
        self.assertEqual(self.mensajes(self.registro(contrasena="corta")), ["Contraseña: Debe tener al menos 8 caracteres."])
        self.assertEqual(self.mensajes(self.registro(contrasena="x" * 73)), ["Contraseña: La contraseña no puede tener más de 72 bytes."])
        toma = self.toma()
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"ubicacion": "x" * 101})), ["Ubicación: No puede tener más de 100 caracteres."])
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"nombre": "  "})), ["Nombre: Debe tener al menos 1 caracteres."])

    def test_numero_fuera_de_rango(self):
        toma = self.toma()
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"umbral_apagado_soc": 150})), ["Umbral de apagado: Debe ser menor o igual que 100."])
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"umbral_apagado_soc": -1})), ["Umbral de apagado: Debe ser mayor o igual que 0."])
        respuesta = self.client.put(self.ruta("/configuracion"), json={"bateria": {"tipo": "agm", "capacidad_wh": 0}}, headers=self.auth())
        self.assertEqual(self.mensajes(respuesta), ["Capacidad: Debe ser mayor que 0."])
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"umbral_apagado_soc": "abc"})), ["Umbral de apagado: Debe ser un número."])

    def test_campo_obligatorio(self):
        respuesta = self.client.post("/auth/registro", json={"nombre": "N"}, headers=self.auth())
        self.assertEqual(
            set(self.mensajes(respuesta)), {"Correo: Es obligatorio.", "Contraseña: Es obligatorio.", "Domo: Es obligatorio."}
        )

    def test_null(self):
        respuesta = self.registro(nombre=None)
        self.assertEqual(self.mensajes(respuesta), ["Nombre: No puede quedar vacío."])
        mensaje = self.mensajes(self.patch_dispositivo(self.toma(), {"nombre": None}))[0]
        self.assertIn("no pueden quedar vacíos: nombre", mensaje)

    def test_opcion_invalida_campo_desconocido_y_fecha(self):
        toma = self.toma()
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"categoria": "otra"})), ["Categoría: No es una opción válida."])
        self.assertEqual(self.mensajes(self.patch_dispositivo(toma, {"prioridad": 1})), ["Prioridad: Este campo no se admite."])
        respuesta = self.client.get(self.ruta("/reportes/resumen"), params={"desde": "ayer", "hasta": "2026-01-01"}, headers=self.auth())
        self.assertEqual(self.mensajes(respuesta), ["Fecha inicial: No es una fecha válida."])

    def test_mensajes_propios_sin_prefijo(self):
        mensaje = self.mensajes(self.crear({"tipo": "bombillo", "nombre": "B", "categoria": "confort"}))[0]
        self.assertEqual(mensaje, "Un bombillo siempre es de categoría 'esencial'.")

    def test_json_mal_formado(self):
        respuesta = self.client.post("/auth/login", content="{no", headers={"Content-Type": "application/json"})
        self.assertEqual(self.mensajes(respuesta)[0].split(": ")[-1], "El contenido enviado no es JSON válido.")

    def test_nunca_repite_lo_recibido(self):
        respuesta = self.registro(contrasena="corta")
        self.assertNotIn("corta", respuesta.text.replace("Contraseña", ""))
        self.assertNotIn('"input"', respuesta.text)
        self.assertNotIn('"ctx"', respuesta.text)

    def test_conserva_tipo_y_ubicacion(self):
        detalle = self.registro(contrasena="corta").json()["detail"][0]
        self.assertEqual((detalle["type"], detalle["loc"]), ("string_too_short", ["body", "contrasena"]))

    def test_ningun_mensaje_queda_en_ingles(self):
        toma = self.toma()
        respuestas = [
            self.registro(correo=5),
            self.registro(nombre=5),
            self.registro(telefono=[]),
            self.client.post("/auth/registro", json=[1], headers=self.auth()),
            self.patch_dispositivo(toma, {"activo": "tal vez"}),
            self.patch_dispositivo(toma, {"brillo": 1.5}),
            self.client.get(self.ruta("/lecturas-bateria"), params={"limite": 99999}, headers=self.auth()),
            self.client.get("/domos/abc", headers=self.auth()),
        ]
        ingles = ("Input should", "should be", "Field required", "value is not", "valid", "String ", "Extra inputs")
        for respuesta in respuestas:
            for mensaje in self.mensajes(respuesta):
                with self.subTest(mensaje=mensaje):
                    for palabra in ingles:
                        self.assertNotIn(palabra, mensaje.replace("válid", ""))
