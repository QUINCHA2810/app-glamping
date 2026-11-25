"""Esquema bi (migración a9e4c1f7b3d2) sin Postgres, y la protección de las pruebas.

Las vistas mismas se prueban en tests/test_bi_postgres.py.
"""

import ast
import importlib
from pathlib import Path
from unittest import TestCase

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from servicios import categorias
from tests import url_permitida
from tests.base import _modulo_migracion
from tests.test_reportes import _cargar_exportador

TESTS = Path(__file__).resolve().parent
MIGRACION = _modulo_migracion("a9e4c1f7b3d2_esquema_bi_para_power_bi")
# Módulos de la aplicación que no se pueden importar antes que tests.base.
DE_LA_APP = {"database", "models", "main", "security", "schemas", "servicios", "routers", "mqtt_publisher", "mqtt_listener"}


class ConstantesDeLaMigracion(TestCase):
    """La migración lleva copias congeladas: si cambian en Python, hace falta otra migración."""

    def test_iguales_a_categorias(self):
        self.assertEqual(MIGRACION.RESERVA_SOC_POR_BATERIA, categorias.RESERVA_SOC_POR_BATERIA)
        self.assertEqual(MIGRACION.TIPO_BATERIA_POR_DEFECTO, categorias.TIPO_BATERIA_POR_DEFECTO)
        self.assertEqual(MIGRACION.PERFILES, categorias.PERFILES)
        self.assertEqual(MIGRACION.PERFIL_POR_DEFECTO, categorias.PERFIL_POR_DEFECTO)

    def test_iguales_a_reportes_y_exportacion(self):
        from servicios.reportes import HUECO_MAXIMO_S

        exportador = _cargar_exportador()
        self.assertEqual((MIGRACION.ORIGEN_SINTETICO, MIGRACION.ORIGEN_REAL), (exportador.ORIGEN_SINTETICO, exportador.ORIGEN_REAL))
        self.assertEqual(MIGRACION.HUECO_MAXIMO_S, HUECO_MAXIMO_S)

    def test_ninguna_vista_lee_usuarios_ni_id_fisico(self):
        for nombre, sql in MIGRACION.SQL_VISTAS.items():
            with self.subTest(vista=nombre):
                minusculas = sql.lower()
                for prohibido in ("usuarios", "usuario_id", "correo", "contrasena", "hash", "token", "telefono"):
                    self.assertNotIn(prohibido, minusculas)
                # La única mención de id_fisico es para decir si es físico, sin el valor.
                self.assertEqual(minusculas.count("id_fisico"), int(nombre == "v_dispositivos"))
                if nombre == "v_dispositivos":
                    self.assertIn('(x.id_fisico is not null) as "real"', minusculas)

    def test_cada_vista_creada_y_con_permiso(self):
        self.assertEqual(set(MIGRACION.SQL_VISTAS), set(MIGRACION.VISTAS))
        for nombre in MIGRACION.VISTAS:
            self.assertIn(f"CREATE VIEW bi.{nombre} AS", MIGRACION.SQL_VISTAS[nombre])
            self.assertNotIn("security_invoker", MIGRACION.SQL_VISTAS[nombre])

    def test_sin_contrasenas(self):
        textos = [Path(MIGRACION.__file__).read_text(encoding="utf-8"), (TESTS / "consulta_previa_a9e4c1f7b3d2.sql").read_text(encoding="utf-8")]
        for texto in textos:
            self.assertNotIn("PASSWORD", texto.upper())


class Proteccion(TestCase):
    def test_url_permitida(self):
        for url in ("sqlite://", "sqlite:///archivo.db", "postgresql://u:p@localhost:5433/x", "postgresql://u@127.0.0.1/x"):
            with self.subTest(url=url):
                self.assertTrue(url_permitida(url))
        for url in (
            "postgresql://u:p@db.abcdefghijklmnop.supabase.co:5432/postgres",
            "postgresql://u.ref:p@aws-0-us-west-2.pooler.supabase.com:5432/postgres",
            "postgresql:///sin_host",
            "mysql://u@192.168.1.10/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(url_permitida(url))

    def test_create_engine_falla_de_inmediato_fuera_de_localhost(self):
        import sqlalchemy
        from sqlalchemy import engine_from_config
        from sqlalchemy.engine import create as modulo

        remota = "postgresql://u:clave-falsa@db.ejemplo.supabase.co:5432/postgres"
        for crear in (sqlalchemy.create_engine, sqlalchemy.engine.create_engine, modulo.create_engine):
            with self.assertRaises(RuntimeError) as error:
                crear(remota)
            self.assertNotIn("clave-falsa", str(error.exception))
        with self.assertRaises(RuntimeError):
            engine_from_config({"sqlalchemy.url": remota})
        sqlalchemy.create_engine("sqlite://").dispose()

    def test_la_aplicacion_usa_sqlite(self):
        database = importlib.import_module("database")
        self.assertEqual(database.engine.url.get_backend_name(), "sqlite")

    def test_cada_archivo_importa_tests_base_antes_que_la_aplicacion(self):
        for ruta in sorted(TESTS.glob("test_*.py")):
            with self.subTest(archivo=ruta.name):
                arbol = ast.parse(ruta.read_text(encoding="utf-8"))
                base_visto = False
                for nodo in arbol.body:
                    if isinstance(nodo, ast.Import):
                        nombres = [a.name for a in nodo.names]
                    elif isinstance(nodo, ast.ImportFrom):
                        nombres = [nodo.module or ""]
                    else:
                        continue
                    for nombre in nombres:
                        if nombre == "tests.base" or nombre.startswith("tests.test_"):
                            base_visto = True
                        elif nombre.split(".")[0] in DE_LA_APP:
                            self.assertTrue(base_visto, f"{ruta.name} importa {nombre} antes que tests.base")
