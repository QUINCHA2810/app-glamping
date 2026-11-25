"""Cuentas de administrador: crear (con su domo), listar, asignar domos, desactivar y
permisos. Modelo v2: todo sale de la tabla asignaciones; no hay creado_por_id."""

import json

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import ROL_ADMINISTRADOR, Asignacion, Usuario
from security import hash_password
from tests.base import PruebaApi

CLAVE = "clave-inicial-1"


class Base(PruebaApi):
    def crear_admin(self, correo="nuevo@prueba.co", usuario=None, temporal=False, domo=None, **campos):
        """Crea la cuenta por la API, asignada a `domo` (por defecto el Domo A). Salvo
        con temporal=True, la deja como si ya hubiera cambiado su contraseña inicial."""
        cuerpo = {"nombre": "Nuevo", "correo": correo, "contrasena": CLAVE, "domo_id": (domo or self.domo_a).id, **campos}
        respuesta = self.client.post("/auth/registro", json=cuerpo, headers=self.auth(usuario))
        if respuesta.status_code == 201 and not temporal:
            self.db.expire_all()
            self.db.get(Usuario, respuesta.json()["id"]).debe_cambiar_contrasena = False
            self.db.commit()
        return respuesta

    def listar(self, usuario=None):
        respuesta = self.client.get("/administradores", headers=self.auth(usuario))
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()

    def en_lista(self, correo, usuario=None):
        return next(a for a in self.listar(usuario) if a["correo"] == correo)

    def patch(self, admin_id, cuerpo, usuario=None):
        return self.client.patch(f"/administradores/{admin_id}", json=cuerpo, headers=self.auth(usuario))

    def asignar_domo(self, correo, domo=None, usuario=None):
        return self.client.post(self.ruta("/administradores", domo), json={"correo": correo}, headers=self.auth(usuario))

    def quitar_domo(self, admin_id, domo=None, usuario=None):
        return self.client.patch(
            self.ruta(f"/administradores/{admin_id}", domo), json={"estado_asignacion": "inactivo"}, headers=self.auth(usuario)
        )

    def login(self, correo, contrasena=CLAVE):
        return self.client.post("/auth/login", json={"correo": correo, "contrasena": contrasena})

    def cabeceras_de(self, correo):
        return {"Authorization": f"Bearer {self.login(correo).json()['access_token']}"}


class Crear(Base):
    def test_crea_administrador_activo_asignado_a_su_domo(self):
        respuesta = self.crear_admin()
        self.assertEqual(respuesta.status_code, 201, respuesta.text)
        self.assertEqual((respuesta.json()["rol"], respuesta.json()["estado_cuenta"]), ("administrador", "activo"))
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 200)
        # Nace con su asignación: ve el domo desde el primer momento.
        domos = self.client.get("/domos", headers=self.cabeceras_de("nuevo@prueba.co")).json()
        self.assertEqual([d["id"] for d in domos], [self.domo_a.id])

    def test_sin_domo_id_da_422_y_no_crea_nada(self):
        cuerpo = {"nombre": "N", "correo": "a@prueba.co", "contrasena": CLAVE}
        self.assertEqual(self.client.post("/auth/registro", json=cuerpo, headers=self.auth()).status_code, 422)
        self.assertIsNone(self.db.query(Usuario).filter(Usuario.correo == "a@prueba.co").first())

    def test_domo_ajeno_da_404_y_domo_que_solo_administra_da_403(self):
        ajeno = self.usuario("ajeno@prueba.co")
        domo_ajeno = self.domo("Domo ajeno", ajeno)
        # El dueño del Domo A también administra el Domo ajeno: lo ve, pero no es suyo.
        domo_compartido = self.domo("Domo compartido", ajeno)
        self.asignar(self.dueno, domo_compartido)
        self.db.commit()
        self.assertEqual(self.crear_admin("a@prueba.co", domo=domo_ajeno).status_code, 404)
        self.assertEqual(self.crear_admin("b@prueba.co", domo=domo_compartido).status_code, 403)
        self.assertEqual(self.db.query(Usuario).filter(Usuario.correo.in_(["a@prueba.co", "b@prueba.co"])).count(), 0)

    def test_validaciones(self):
        for cuerpo in (
            {"contrasena": "corta12"},
            {"contrasena": "x" * 73},
            {"contrasena": "ñ" * 37},
            {"nombre": "   "},
            {"correo": "no-es-correo"},
            {"domo_id": "uno"},
        ):
            with self.subTest(cuerpo=cuerpo):
                completo = {"nombre": "N", "correo": "a@prueba.co", "contrasena": CLAVE, "domo_id": self.domo_a.id, **cuerpo}
                self.assertEqual(self.client.post("/auth/registro", json=completo, headers=self.auth()).status_code, 422)

    def test_contrasena_justo_en_los_limites(self):
        self.assertEqual(self.crear_admin("a@prueba.co", contrasena="x" * 8).status_code, 201)
        self.assertEqual(self.crear_admin("b@prueba.co", contrasena="x" * 72).status_code, 201)

    def test_correo_repetido_da_409_sin_asignar_nada(self):
        self.crear_admin()
        antes = self.db.query(Asignacion).count()
        self.assertEqual(self.crear_admin().status_code, 409)
        self.assertEqual(self.db.query(Asignacion).count(), antes)

    def test_no_se_elige_el_rol(self):
        respuesta = self.crear_admin(rol="dueño")
        self.assertEqual(self.db.query(Usuario).filter(Usuario.correo == "nuevo@prueba.co").one().rol, "administrador")
        self.assertEqual(respuesta.json()["rol"], "administrador")


class SinSecretos(Base):
    def test_ninguna_respuesta_lleva_hash_ni_token(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        creado = self.crear_admin()
        admin_id = creado.json()["id"]
        hash_real = self.db.query(Usuario).filter(Usuario.correo == "nuevo@prueba.co").one().contrasena_hash
        respuestas = [
            creado,
            self.client.get("/administradores", headers=self.auth()),
            self.patch(admin_id, {"nombre": "Otro"}),
            self.client.get(self.ruta("/administradores"), headers=self.auth()),
            self.asignar_domo("nuevo@prueba.co", otro),
            self.client.get("/users/me", headers=self.auth()),
        ]
        for respuesta in respuestas:
            with self.subTest(url=str(respuesta.url)):
                texto = respuesta.text
                self.assertEqual(respuesta.status_code in (200, 201), True, texto)
                self.assertNotIn(hash_real, texto)
                self.assertNotIn(CLAVE, texto)
                # El único campo con esa palabra es el indicador booleano de contraseña temporal.
                cuerpo = json.dumps(respuesta.json()).lower().replace('"debe_cambiar_contrasena"', "")
                for campo in ("contrasena", "hash", "token"):
                    self.assertNotIn(campo, cuerpo)


class Listar(Base):
    def test_trae_domos_y_estado(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        self.crear_admin()
        self.assertEqual([d["nombre_domo"] for d in self.en_lista("nuevo@prueba.co")["domos"]], ["Domo A"])
        self.asignar_domo("nuevo@prueba.co", otro)
        admin = self.en_lista("nuevo@prueba.co")
        self.assertEqual([d["nombre_domo"] for d in admin["domos"]], ["Domo A", "Domo B"])
        self.assertEqual({d["estado_asignacion"] for d in admin["domos"]}, {"activo"})
        self.assertEqual(admin["estado_cuenta"], "activo")
        self.assertTrue(admin["cuenta_propia"])

    def test_un_administrador_solo_de_mis_domos_es_cuenta_propia(self):
        # self.admin se creó directo en la base, asignado solo al Domo A: en v2 eso basta.
        admin = self.en_lista("admin@prueba.co")
        self.assertTrue(admin["cuenta_propia"])
        self.assertEqual([d["domo_id"] for d in admin["domos"]], [self.domo_a.id])

    def test_no_muestra_lo_de_otro_dueno_y_el_compartido_no_es_propio(self):
        ajeno = self.usuario("ajeno@prueba.co")
        domo_ajeno = self.domo("Domo ajeno", ajeno)
        self.db.commit()
        self.crear_admin("suyo@prueba.co", usuario=ajeno, domo=domo_ajeno)
        # Un administrador compartido entre dos dueños.
        self.asignar(self.admin, domo_ajeno)
        self.db.commit()

        correos = [a["correo"] for a in self.listar()]
        self.assertNotIn("suyo@prueba.co", correos)
        self.assertNotIn("ajeno@prueba.co", correos)
        # Cada dueño ve solo sus propios domos, y ninguno puede editarlo solo.
        mio, suyo = self.en_lista("admin@prueba.co"), self.en_lista("admin@prueba.co", ajeno)
        self.assertEqual([d["nombre_domo"] for d in mio["domos"]], ["Domo A"])
        self.assertEqual([d["nombre_domo"] for d in suyo["domos"]], ["Domo ajeno"])
        self.assertFalse(mio["cuenta_propia"])
        self.assertFalse(suyo["cuenta_propia"])

    def test_quitar_un_domo_lo_saca_de_la_lista_de_domos(self):
        otro = self.domo("Domo B", self.dueno)
        self.asignar(self.admin, otro)
        self.db.commit()
        self.assertEqual(self.quitar_domo(self.admin.id).status_code, 200)
        self.assertEqual([d["nombre_domo"] for d in self.en_lista("admin@prueba.co")["domos"]], ["Domo B"])


class Asignaciones(Base):
    def test_asignar_da_acceso_y_quitar_lo_retira(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        admin_id = self.crear_admin().json()["id"]
        cabeceras = self.cabeceras_de("nuevo@prueba.co")
        self.assertEqual(len(self.client.get("/domos", headers=cabeceras).json()), 1)

        self.assertEqual(self.asignar_domo("nuevo@prueba.co", otro).status_code, 201)
        self.assertEqual(len(self.client.get("/domos", headers=cabeceras).json()), 2)
        # Asignar otra vez no duplica.
        self.assertEqual(self.asignar_domo("nuevo@prueba.co", otro).status_code, 200)

        quitar = self.quitar_domo(admin_id, otro)
        self.assertEqual((quitar.status_code, quitar.json()["estado_asignacion"]), (200, "inactivo"))
        self.assertEqual([d["id"] for d in self.client.get("/domos", headers=cabeceras).json()], [self.domo_a.id])
        self.assertEqual(self.client.get(self.ruta("", otro), headers=cabeceras).status_code, 404)

        self.assertEqual(self.asignar_domo("nuevo@prueba.co", otro).status_code, 201)
        self.assertEqual(len(self.client.get("/domos", headers=cabeceras).json()), 2)

    def test_quitar_el_unico_domo_da_409(self):
        respuesta = self.quitar_domo(self.admin.id)
        self.assertEqual(respuesta.status_code, 409, respuesta.text)
        self.assertEqual(self.db.query(Asignacion).filter(Asignacion.usuario_id == self.admin.id).count(), 1)

    def test_solo_administradores_que_ya_tienen_algun_domo_mio(self):
        ajeno = self.usuario("ajeno@prueba.co")
        domo_ajeno = self.domo("Domo ajeno", ajeno)
        self.db.commit()
        self.crear_admin("suyo@prueba.co", usuario=ajeno, domo=domo_ajeno)
        for correo in ("suyo@prueba.co", "ajeno@prueba.co", "no-existe@prueba.co"):
            with self.subTest(correo=correo):
                self.assertEqual(self.asignar_domo(correo).status_code, 404)

    def test_la_lista_del_domo(self):
        filas = self.client.get(self.ruta("/administradores"), headers=self.auth()).json()
        self.assertEqual([(f["administrador_id"], f["estado_asignacion"]) for f in filas], [(self.admin.id, "activo")])


class Cuenta(Base):
    def test_desactivar_bloquea_login_y_token_al_instante(self):
        admin_id = self.crear_admin().json()["id"]
        cabeceras = self.cabeceras_de("nuevo@prueba.co")
        self.assertEqual(self.client.get("/domos", headers=cabeceras).status_code, 200)

        respuesta = self.patch(admin_id, {"estado_cuenta": "inactivo"})
        self.assertEqual(respuesta.json()["estado_cuenta"], "inactivo")
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 403)
        self.assertEqual(self.client.get("/domos", headers=cabeceras).status_code, 403)

        self.patch(admin_id, {"estado_cuenta": "activo"})
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 200)
        self.assertEqual(self.client.get("/domos", headers=cabeceras).status_code, 200)

    def test_editar_datos(self):
        admin_id = self.crear_admin().json()["id"]
        datos = self.patch(admin_id, {"nombre": " Ana ", "apellido": "Díaz", "telefono": "300"}).json()
        self.assertEqual((datos["nombre"], datos["apellido"], datos["telefono"]), ("Ana", "Díaz", "300"))

    def test_cuerpos_invalidos(self):
        admin_id = self.crear_admin().json()["id"]
        for cuerpo in (
            {"estado_cuenta": "borrado"},
            {"estado_cuenta": None},
            {"nombre": None},
            {"nombre": "  "},
            {"correo": "otro@prueba.co"},
            {"rol": "dueño"},
            {"contrasena": "otra-clave-1"},
        ):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.patch(admin_id, cuerpo).status_code, 422)

    def test_solo_las_cuentas_propias(self):
        ajeno = self.usuario("ajeno@prueba.co")
        domo_ajeno = self.domo("Domo ajeno", ajeno)
        self.db.commit()
        suyo = self.crear_admin("suyo@prueba.co", usuario=ajeno, domo=domo_ajeno).json()["id"]
        compartido = self.crear_admin("compartido@prueba.co").json()["id"]
        self.db.add(Asignacion(usuario_id=compartido, domo_id=domo_ajeno.id, rol=ROL_ADMINISTRADOR))
        self.db.commit()
        for admin_id in (suyo, compartido, self.dueno.id, ajeno.id, 9999):
            with self.subTest(admin_id=admin_id):
                self.assertEqual(self.patch(admin_id, {"estado_cuenta": "inactivo"}).status_code, 404)
        self.db.expire_all()
        for usuario_id in (suyo, compartido, self.dueno.id):
            self.assertEqual(self.db.get(Usuario, usuario_id).estado_cuenta, "activo")
        # La que solo atiende mis domos sí.
        self.assertEqual(self.patch(self.admin.id, {"estado_cuenta": "inactivo"}).status_code, 200)


class Permisos(Base):
    def test_el_administrador_no_entra(self):
        admin_id = self.crear_admin().json()["id"]
        self.assertEqual(self.client.get("/administradores", headers=self.auth(self.admin)).status_code, 403)
        self.assertEqual(self.patch(admin_id, {"nombre": "x"}, self.admin).status_code, 403)
        self.assertEqual(self.crear_admin("b@prueba.co", usuario=self.admin).status_code, 403)
        self.assertEqual(self.asignar_domo("nuevo@prueba.co", usuario=self.admin).status_code, 403)

    def test_sin_sesion(self):
        self.assertEqual(self.client.get("/administradores").status_code, 401)
        self.assertEqual(self.client.patch("/administradores/1", json={"nombre": "x"}).status_code, 401)

    def test_login_de_una_cuenta_desactivada(self):
        self.db.add(Usuario(nombre="V", correo="v@prueba.co", contrasena_hash=hash_password(CLAVE), activo=False))
        self.db.commit()
        self.assertEqual(self.login("v@prueba.co").status_code, 403)
