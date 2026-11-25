"""Desactivación en los reportes, contraseña temporal y la herramienta de emergencia."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Accion, Usuario
from security import hash_password, verify_password
from servicios.reportes import resumen_del_domo
from tests.base import PruebaApi
from tests.test_administradores import CLAVE, Base
from tests.test_reportes import DIA_1, DIA_2, exportador, leer, local


class Desactivacion(PruebaApi):
    def test_desactivar_guarda_la_fecha_y_reactivar_la_borra(self):
        toma = self.toma()
        datos = self.patch_dispositivo(toma, {"activo": False}).json()
        self.assertIsNotNone(datos["desactivado_en"])
        primera = datos["desactivado_en"]
        # Volver a enviar false no la mueve.
        self.assertEqual(self.patch_dispositivo(toma, {"activo": False}).json()["desactivado_en"], primera)
        self.assertIsNone(self.patch_dispositivo(toma, {"activo": True}).json()["desactivado_en"])
        self.assertIsNone(self.patch_dispositivo(toma, {"nombre": "x"}).json()["desactivado_en"])


class CortesYDesactivacion(PruebaApi):
    def accion(self, toma, instante, actor_tipo, accion, actor_ref):
        nuevo = "apagado" if accion == "apagar" else "encendido"
        anterior = "encendido" if accion == "apagar" else "apagado"
        self.db.add(
            Accion(
                dispositivo_id=toma.id, accion=accion, estado_anterior=anterior, estado_nuevo=nuevo,
                actor_tipo=actor_tipo, actor_ref=actor_ref, resultado="ok", fecha_hora=instante,
            )
        )

    def cortar(self, toma, instante):
        self.accion(toma, instante, "sistema", "apagar", "R-13")
        self.db.commit()

    def resumen(self, ahora=None):
        self.db.expire_all()
        return resumen_del_domo(self.db, self.domo_a.id, DIA_1, DIA_2, ahora or local(DIA_2, 23))

    def test_el_corte_termina_al_desactivar(self):
        # El caso de "Prueba Vatia": cortada por R-13 y desactivada después.
        toma = self.toma("Prueba Vatia")
        self.cortar(toma, local(DIA_1, 10))
        toma.activo = False
        toma.desactivado_en = local(DIA_1, 12)
        self.db.commit()
        corte = self.resumen()["cortes_por_toma"][0]
        self.assertEqual((corte["cortes"], corte["activo"]), (1, False))
        self.assertAlmostEqual(corte["horas_apagada"], 2.0, places=3)

    def test_sin_desactivar_sigue_hasta_ahora(self):
        toma = self.toma()
        self.cortar(toma, local(DIA_1, 10))
        self.assertAlmostEqual(self.resumen(ahora=local(DIA_1, 15))["cortes_por_toma"][0]["horas_apagada"], 5.0, places=3)

    def test_la_siguiente_accion_manda_si_es_antes(self):
        toma = self.toma()
        self.cortar(toma, local(DIA_1, 10))
        self.accion(toma, local(DIA_1, 11), "dueno", "encender", self.dueno.correo)
        toma.activo = False
        toma.desactivado_en = local(DIA_1, 14)
        self.db.commit()
        self.assertAlmostEqual(self.resumen()["cortes_por_toma"][0]["horas_apagada"], 1.0, places=3)

    def test_desactivada_antes_del_corte_no_suma_negativo(self):
        toma = self.toma()
        self.cortar(toma, local(DIA_1, 10))
        toma.activo = False
        toma.desactivado_en = local(DIA_1, 9)
        self.db.commit()
        self.assertEqual(self.resumen()["cortes_por_toma"][0]["horas_apagada"], 0)

    def test_por_la_api(self):
        toma = self.toma()
        self.cortar(toma, datetime.now(timezone.utc) - timedelta(hours=3))
        self.patch_dispositivo(toma, {"activo": False})
        hoy = datetime.now(timezone(timedelta(hours=-5))).date()
        respuesta = self.client.get(self.ruta("/reportes/resumen"), params={"desde": hoy - timedelta(days=1), "hasta": hoy}, headers=self.auth())
        horas = respuesta.json()["cortes_por_toma"][0]["horas_apagada"]
        self.assertAlmostEqual(horas, 3.0, delta=0.01)
        # Más tarde, sigue en 3 horas.
        mas_tarde = resumen_del_domo(self.db, self.domo_a.id, hoy - timedelta(days=1), hoy, datetime.now(timezone.utc) + timedelta(hours=10))
        self.assertAlmostEqual(mas_tarde["cortes_por_toma"][0]["horas_apagada"], 3.0, delta=0.01)

    def test_exportacion_con_las_mismas_reglas(self):
        toma = self.toma("Prueba Vatia")
        self.cortar(toma, local(DIA_1, 10))
        toma.activo = False
        toma.desactivado_en = local(DIA_1, 12)
        self.db.commit()
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            exportador.exportar(self.db, carpeta, [self.domo_a.id], DIA_1, DIA_2)
            cortes = leer(carpeta, "hechos_cortes.csv")
            self.assertEqual(len(cortes), 1)
            self.assertEqual((cortes[0]["inicio_local"], cortes[0]["fin_local"], cortes[0]["horas_apagada"]), ("2026-03-10 10:00:00", "2026-03-10 12:00:00", "2.0"))
            self.assertEqual(cortes[0]["empieza_en_el_rango"], "1")
            dispositivo = leer(carpeta, "dim_dispositivo.csv")[0]
            self.assertEqual((dispositivo["activo"], dispositivo["desactivado_en_local"]), ("0", "2026-03-10 12:00:00"))


class ContrasenaTemporal(Base):
    def entrar(self, correo, clave=CLAVE):
        return {"Authorization": f"Bearer {self.login(correo, clave).json()['access_token']}"}

    def test_crear_la_deja_temporal_y_bloquea_lo_demas(self):
        admin_id = self.crear_admin(temporal=True).json()["id"]
        self.asignar_domo("nuevo@prueba.co")
        cabeceras = self.entrar("nuevo@prueba.co")
        perfil = self.client.get("/users/me", headers=cabeceras)
        self.assertEqual(perfil.status_code, 200)
        self.assertTrue(perfil.json()["debe_cambiar_contrasena"])
        for metodo, ruta in (
            ("GET", "/domos"),
            ("GET", f"/domos/{self.domo_a.id}/dispositivos"),
            ("PUT", "/users/me"),
            ("GET", f"/domos/{self.domo_a.id}/reportes/resumen?desde=2026-01-01&hasta=2026-01-02"),
            ("POST", f"/domos/{self.domo_a.id}/control-energetico/evaluar"),
        ):
            with self.subTest(ruta=ruta):
                respuesta = self.client.request(metodo, ruta, json={}, headers=cabeceras)
                self.assertEqual(respuesta.status_code, 403)
                self.assertEqual(respuesta.json()["detail"], "Tu contraseña es temporal. Cámbiala en Mi cuenta para poder continuar.")
        self.assertIsNotNone(admin_id)

    def test_cambiarla_en_mi_cuenta_lo_libera(self):
        self.crear_admin(temporal=True)
        cabeceras = self.entrar("nuevo@prueba.co")
        respuesta = self.client.post("/users/me/contrasena", json={"contrasena_actual": CLAVE, "contrasena_nueva": "definitiva-1"}, headers=cabeceras)
        self.assertEqual(respuesta.status_code, 200)
        nuevas = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}
        self.assertFalse(self.client.get("/users/me", headers=nuevas).json()["debe_cambiar_contrasena"])
        self.assertEqual(self.client.get("/domos", headers=nuevas).status_code, 200)

    def test_restablecer_la_vuelve_temporal(self):
        admin_id = self.crear_admin().json()["id"]
        self.assertEqual(self.client.get("/domos", headers=self.entrar("nuevo@prueba.co")).status_code, 200)
        self.client.post(f"/administradores/{admin_id}/restablecer-contrasena", json={"contrasena_temporal": "temporal-123"}, headers=self.auth())
        cabeceras = self.entrar("nuevo@prueba.co", "temporal-123")
        self.assertEqual(self.client.get("/domos", headers=cabeceras).status_code, 403)
        self.assertTrue(self.client.get("/users/me", headers=cabeceras).json()["debe_cambiar_contrasena"])

    def test_el_login_sigue_funcionando(self):
        self.crear_admin(temporal=True)
        self.assertEqual(self.login("nuevo@prueba.co").status_code, 200)

    def test_las_cuentas_existentes_no_quedan_bloqueadas(self):
        self.assertEqual(self.client.get("/domos", headers=self.auth(self.admin)).status_code, 200)
        self.assertFalse(self.client.get("/users/me", headers=self.auth()).json()["debe_cambiar_contrasena"])

    def test_la_lista_de_administradores_no_expone_el_dato_de_mas(self):
        self.crear_admin(temporal=True)
        self.assertNotIn("hash", str(self.listar()))


class Herramienta(PruebaApi):

    @classmethod
    def setUpClass(cls):
        import importlib.util

        ruta = Path(__file__).resolve().parent.parent / "tools" / "restablecer_contrasena.py"
        spec = importlib.util.spec_from_file_location("restablecer_contrasena", ruta)
        cls.modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.modulo)

    def setUp(self):
        super().setUp()
        self.dueno.contrasena_hash = hash_password("olvidada-1")
        self.dueno.debe_cambiar_contrasena = True
        self.db.commit()
        self.dichos = []

    def correr(self, respuestas, claves):
        respuestas, claves = iter(respuestas), iter(claves)
        return self.modulo.restablecer(self.db, lambda _: next(respuestas), lambda _: next(claves), self.dichos.append)

    def recargado(self):
        self.db.expire_all()
        return self.db.get(Usuario, self.dueno.id)

    def test_cambia_la_contrasena_cierra_sesiones_y_quita_la_temporal(self):
        version = self.dueno.version_sesion
        token_viejo = self.auth()
        self.assertTrue(self.correr(["dueno@prueba.co", "s"], ["nueva-clave-1", "nueva-clave-1"]))
        usuario = self.recargado()
        self.assertTrue(verify_password("nueva-clave-1", usuario.contrasena_hash))
        self.assertEqual(usuario.version_sesion, version + 1)
        self.assertFalse(usuario.debe_cambiar_contrasena)
        self.assertEqual(self.client.get("/users/me", headers=token_viejo).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"correo": "dueno@prueba.co", "contrasena": "nueva-clave-1"}).status_code, 200)

    def test_muestra_nombre_y_rol_y_nunca_la_clave_ni_el_hash(self):
        self.correr(["dueno@prueba.co", "s"], ["nueva-clave-1", "nueva-clave-1"])
        texto = "\n".join(self.dichos)
        self.assertIn("dueno@prueba.co", self.dueno.correo)
        self.assertIn("Dueño", texto)
        self.assertNotIn("nueva-clave-1", texto)
        self.assertNotIn(self.recargado().contrasena_hash, texto)
        self.assertNotIn("$2", texto)

    def test_correo_desconocido(self):
        self.assertFalse(self.correr(["nadie@prueba.co"], []))
        self.assertTrue(verify_password("olvidada-1", self.recargado().contrasena_hash))

    def test_sin_confirmar_no_cambia_nada(self):
        self.assertFalse(self.correr(["dueno@prueba.co", "n"], []))
        self.assertTrue(verify_password("olvidada-1", self.recargado().contrasena_hash))

    def test_valida_y_reintenta(self):
        self.assertTrue(self.correr(["dueno@prueba.co", "s"], ["corta", "nueva-clave-1", "otra-cosa-1", "nueva-clave-1", "nueva-clave-1"]))
        self.assertIn("Debe tener al menos 8 caracteres.", "\n".join(self.dichos))
        self.assertIn("No coinciden.", self.dichos)

    def test_tres_intentos_fallidos_no_cambian_nada(self):
        self.assertFalse(self.correr(["dueno@prueba.co", "s"], ["corta", "x" * 73, "a-clave-1", "b-clave-1"]))
        self.assertTrue(verify_password("olvidada-1", self.recargado().contrasena_hash))

    def test_el_codigo_no_imprime_la_conexion(self):
        fuente = (Path(__file__).resolve().parent.parent / "tools" / "restablecer_contrasena.py").read_text(encoding="utf-8")
        self.assertNotIn("DATABASE_URL", fuente.split('"""', 2)[2])
        self.assertNotIn("print(usuario.contrasena_hash", fuente)


