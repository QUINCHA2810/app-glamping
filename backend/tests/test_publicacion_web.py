"""Publicación en Internet (docs/publicacion_web.md): límite de intentos de inicio de
sesión, documentación cerrada con API_DOCS=false, secreto JWT y cabeceras de seguridad."""

import os
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from fastapi import FastAPI
from fastapi.testclient import TestClient

import limite_login
from main import app
from publicacion import CABECERAS_SEGURIDAD, CabecerasSeguridad, docs_habilitadas, opciones_documentacion
from security import hash_password, validar_secreto
from tests.base import PruebaApi

CLAVE = "clave-correcta-1"
MENSAJE_401 = "Correo o contraseña incorrectos."


class LimiteLogin(PruebaApi):
    def setUp(self):
        super().setUp()
        self.dueno.contrasena_hash = hash_password(CLAVE)
        self.db.commit()
        self.ahora = 1000.0
        parche = patch.object(limite_login, "reloj", lambda: self.ahora)
        parche.start()
        self.addCleanup(parche.stop)

    def cliente(self, host="203.0.113.7"):
        return TestClient(app, raise_server_exceptions=False, client=(host, 50000))

    def login(self, correo="dueno@prueba.co", contrasena="mala", cliente=None, xff=None):
        cabeceras = {"X-Forwarded-For": xff} if xff is not None else {}
        return (cliente or self.cliente()).post(
            "/auth/login", json={"correo": correo, "contrasena": contrasena}, headers=cabeceras
        )

    def test_al_sexto_fallo_da_429_aunque_la_contrasena_sea_correcta(self):
        for _ in range(5):
            self.assertEqual(self.login().status_code, 401)
        respuesta = self.login(contrasena=CLAVE)
        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()["detail"], limite_login.MENSAJE_BLOQUEO)

    def test_el_429_no_dice_si_el_correo_existe(self):
        for correo in ("dueno@prueba.co", "nadie@prueba.co"):
            limite_login.reiniciar()
            for _ in range(5):
                self.assertEqual(self.login(correo=correo).json()["detail"], MENSAJE_401)
            self.assertEqual(self.login(correo=correo).json()["detail"], limite_login.MENSAJE_BLOQUEO)

    def test_un_inicio_correcto_limpia_el_contador_del_correo(self):
        for _ in range(4):
            self.login()
        self.assertEqual(self.login(contrasena=CLAVE).status_code, 200)
        # Otro cliente: el contador de la IP anterior no cuenta aquí.
        otro = self.cliente("198.51.100.9")
        for _ in range(4):
            self.assertEqual(self.login(cliente=otro).status_code, 401)
        self.assertEqual(self.login(contrasena=CLAVE, cliente=otro).status_code, 200)

    def test_limite_por_ip_con_correos_distintos(self):
        cliente = self.cliente()
        for i in range(5):
            self.assertEqual(self.login(correo=f"c{i}@prueba.co", cliente=cliente).status_code, 401)
        self.assertEqual(self.login(contrasena=CLAVE, cliente=cliente).status_code, 429)
        # Desde otra IP, el dueño sí entra.
        self.assertEqual(self.login(contrasena=CLAVE, cliente=self.cliente("198.51.100.9")).status_code, 200)

    def test_limite_por_correo_desde_ips_distintas(self):
        for i in range(5):
            self.assertEqual(self.login(cliente=self.cliente(f"198.51.100.{i + 1}")).status_code, 401)
        self.assertEqual(self.login(contrasena=CLAVE, cliente=self.cliente("198.51.100.50")).status_code, 429)

    def test_pasada_la_ventana_se_vuelve_a_permitir(self):
        for _ in range(5):
            self.login()
        self.assertEqual(self.login(contrasena=CLAVE).status_code, 429)
        self.ahora += limite_login.VENTANA_S
        self.assertEqual(self.login(contrasena=CLAVE).status_code, 200)

    def test_desde_el_proxy_usa_la_ip_real_de_x_forwarded_for(self):
        proxy = self.cliente("127.0.0.1")
        # Cada intento con un correo distinto: solo puede bloquear el límite por IP.
        for i in range(5):
            self.login(correo=f"c{i}@prueba.co", cliente=proxy, xff="1.2.3.4, 203.0.113.7, 127.0.0.1")
        # La entrada falsa de la izquierda (1.2.3.4) cambia, pero la IP real es la misma.
        respuesta = self.login(correo="otro@prueba.co", cliente=proxy, xff="9.9.9.9, 203.0.113.7")
        self.assertEqual(respuesta.status_code, 429)
        respuesta = self.login(correo="otro@prueba.co", cliente=proxy, xff="203.0.113.8")
        self.assertEqual(respuesta.status_code, 401)

    def test_desde_el_proxy_sin_ip_real_solo_aplica_el_limite_por_correo(self):
        proxy = self.cliente("127.0.0.1")
        with self.assertLogs("uvicorn.error.limite_login", "INFO") as registro:
            for i in range(6):
                self.assertEqual(self.login(correo=f"c{i}@prueba.co", cliente=proxy).status_code, 401)
            for i in range(6):
                respuesta = self.login(correo=f"d{i}@prueba.co", cliente=proxy, xff="127.0.0.1, ::1")
                self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(len(registro.output), 12)
        self.assertTrue(all("sin IP real" in linea for linea in registro.output))
        # El límite por correo sigue activo.
        for _ in range(5):
            self.login(cliente=proxy)
        self.assertEqual(self.login(contrasena=CLAVE, cliente=proxy).status_code, 429)

    def test_x_forwarded_for_se_ignora_si_no_viene_del_proxy(self):
        cliente = self.cliente("203.0.113.7")
        with self.assertLogs("uvicorn.error.limite_login", "INFO") as registro:
            for i in range(5):
                self.login(correo=f"c{i}@prueba.co", cliente=cliente, xff=f"10.0.0.{i + 1}")
        self.assertTrue(all("203.0.113.7" in linea for linea in registro.output))
        self.assertEqual(self.login(contrasena=CLAVE, cliente=cliente, xff="10.0.0.99").status_code, 429)


class Documentacion(TestCase):
    def test_api_docs_por_defecto_abierta(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_DOCS", None)
            self.assertTrue(docs_habilitadas())

    def test_api_docs_false_cierra_docs_redoc_y_openapi(self):
        for valor in ("false", "FALSE", "0", "no", " off "):
            with patch.dict(os.environ, {"API_DOCS": valor}):
                self.assertFalse(docs_habilitadas(), valor)
                cliente = TestClient(FastAPI(**opciones_documentacion()))
                for ruta in ("/docs", "/redoc", "/openapi.json"):
                    self.assertEqual(cliente.get(ruta).status_code, 404, (valor, ruta))

    def test_api_docs_true_las_deja(self):
        with patch.dict(os.environ, {"API_DOCS": "true"}):
            cliente = TestClient(FastAPI(**opciones_documentacion()))
            self.assertEqual(cliente.get("/openapi.json").status_code, 200)


class SecretoJwt(TestCase):
    def test_secreto_largo_no_dice_nada(self):
        with self.assertNoLogs("uvicorn.error.security"):
            validar_secreto("x" * 32, publico=True)

    def test_secreto_corto_registra_error_en_desarrollo(self):
        with self.assertLogs("uvicorn.error.security", "ERROR") as registro:
            validar_secreto("corto", publico=False)
        self.assertIn("JWT_SECRET_KEY", registro.output[0])

    def test_secreto_corto_impide_arrancar_en_modo_publico(self):
        with self.assertLogs("uvicorn.error.security", "ERROR"), self.assertRaises(RuntimeError):
            validar_secreto("x" * 31, publico=True)


class Cabeceras(PruebaApi):
    def test_en_respuestas_normales_y_de_error(self):
        for respuesta in (
            self.client.get("/"),
            self.client.get("/users/me"),
            self.client.post("/auth/login", content="{no", headers={"Content-Type": "application/json"}),
        ):
            for nombre, valor in CABECERAS_SEGURIDAD.items():
                self.assertEqual(respuesta.headers.get(nombre), valor, (respuesta.request.url, nombre))
            self.assertNotIn("strict-transport-security", respuesta.headers)

    def test_no_pisa_una_cabecera_que_ya_trae_la_respuesta(self):
        mini = FastAPI()
        mini.add_middleware(CabecerasSeguridad)

        @mini.get("/")
        def raiz():
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

        respuesta = TestClient(mini).get("/")
        self.assertEqual(respuesta.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(respuesta.headers["X-Content-Type-Options"], "nosniff")
