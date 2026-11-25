"""
Migración c4a8e2f7d1b6 contra un Postgres de verdad, vacío y desechable.

Se omite si TEST_POSTGRES_URL no está definida, y se niega a correr si no apunta
a esta máquina: borra el esquema public.

    TEST_POSTGRES_URL=postgresql://postgres:clave@localhost:5433/postgres python -m unittest tests.test_migracion_postgres
"""

# Modelo v2 (ronda DB-2): este módulo prueba la línea v1 y depende de sus modelos.
import unittest  # noqa: E402

raise unittest.SkipTest(
    "Línea de migraciones v1 (backend/alembic) y su API sobre el esquema v1: en esta rama la aplicación usa el modelo v2. Sus pruebas de API sobre Postgres tienen el equivalente v2 en tests/test_esquema_v2_postgres.py (ApiSobrePostgres). Para correrlas, la etiqueta v1-modelo-actual."
)


import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlparse
import tempfile
from datetime import datetime, timedelta, timezone

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Domo, LecturaBateria, Usuario
from schemas import LIMITES_TELEMETRIA
from security import crear_token_acceso
from tests.base import _modulo_migracion, importar_listener
from tests.test_reportes import _cargar_exportador
from tests.test_reportes import DIA_1, DIA_2, Comprobaciones, ComprobacionesExportacion, escenario
from tests.test_telemetria_y_globales import CAMPOS_POR_RUTA

URL = os.environ.get("TEST_POSTGRES_URL")
BACKEND = Path(__file__).resolve().parent.parent
BASE_FASE_1 = "e1857d5e5e9d"
ANTERIOR = "b7d2c4e8a1f3"
NUEVA = "f7d3b9e5a2c8"
PREVIA_R6C = "d5b1f3a9c2e7"
R6C = "e6c2a8d4f1b9"

# La consulta de solo lectura que se corre en Supabase antes de aplicar e6c2a8d4f1b9.
CONSULTA_PREVIA_R6C = (Path(__file__).resolve().parent / "consulta_previa_e6c2a8d4f1b9.sql").read_text(encoding="utf-8")
CONSULTA_PREVIA_R7 = (Path(__file__).resolve().parent / "consulta_previa_f7d3b9e5a2c8.sql").read_text(encoding="utf-8")


def alembic(*argumentos):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *argumentos],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": URL, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
    )


# Índices que Supabase tiene, creados a mano fuera de la cadena de migraciones. Las
# preparaciones que llegan a head los agregan para parecerse a producción.
INDICES_SUPABASE = (
    ("idx_lecturas_bateria_domo_fecha", "lecturas_bateria"),
    ("idx_lecturas_consumo_domo_fecha", "lecturas_consumo"),
    ("idx_lecturas_fv_domo_fecha", "lecturas_fotovoltaicas"),
    ("idx_lecturas_sensores_domo_fecha", "lecturas_sensores"),
    ("idx_historial_domo_fecha", "historial_acciones"),
)


def indices_de_supabase(conexion):
    for nombre, tabla in INDICES_SUPABASE:
        conexion.execute(text(f"CREATE INDEX {nombre} ON public.{tabla} (domo_id, fecha_hora)"))


def vaciar(engine):
    """Deja la base como recién creada: sin public ni bi, y sin el rol lector_bi de
    a9e4c1f7b3d2, que es de todo el servidor y sobreviviría al esquema."""
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS bi CASCADE"))
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'lector_bi'")).scalar():
            c.execute(text("DROP OWNED BY lector_bi"))
            c.execute(text("DROP ROLE lector_bi"))


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class Migracion(TestCase):
    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        vaciar(self.engine)
        # La cadena de migraciones desde cero no reproduce del todo el esquema de
        # Supabase: se le agrega lo que la fase 1 espera encontrar y las columnas
        # de umbral como `real`, que es su tipo allá y afecta al CHECK de la banda.
        self.subir(BASE_FASE_1)
        with self.engine.begin() as c:
            for nombre, tabla, condicion in _modulo_migracion("b7d2c4e8a1f3_integridad_fase_1").CHECK_ANTIGUOS:
                c.execute(text(f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} CHECK ({condicion})"))
            for columna in ("umbral_apagado_soc", "umbral_reactivacion_soc"):
                c.execute(text(f"ALTER TABLE dispositivos ALTER COLUMN {columna} TYPE real"))
        self.subir(ANTERIOR)
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO usuarios (id, nombre, correo, contrasena_hash, rol, estado_cuenta) "
                "VALUES (1, 'x', 'x@x.co', 'x', 'dueño', 'activo')"
            ))
            c.execute(text("INSERT INTO domos (id, propietario_id, nombre, estado_domo) VALUES (1, 1, 'd', 'activo')"))

    def subir(self, destino=NUEVA):
        resultado = alembic("upgrade", destino)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def dispositivo(self, nombre, tipo="toma", apagado=None, reactivacion=None, prioridad=None):
        with self.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO dispositivos (domo_id, tipo, nombre, estado, prioridad, umbral_apagado_soc, umbral_reactivacion_soc) "
                    "VALUES (1, :tipo, :nombre, 'apagado', :prioridad, :apagado, :reactivacion)"
                ),
                dict(tipo=tipo, nombre=nombre, prioridad=prioridad, apagado=apagado, reactivacion=reactivacion),
            )

    def filas(self):
        with self.engine.connect() as c:
            consulta = "SELECT nombre, categoria, prioridad, umbral_apagado_soc, umbral_reactivacion_soc, activo, ubicacion FROM dispositivos"
            return {f.nombre: f for f in c.execute(text(consulta))}

    def version(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()

    def test_relleno(self):
        self.dispositivo("bombillo", "bombillo", prioridad=3)
        self.dispositivo("otro", "otro")
        self.dispositivo("sin-umbrales", prioridad=99)
        self.dispositivo("exacta-i", apagado=20, reactivacion=30, prioridad=1)
        self.dispositivo("exacta-c", apagado=35, reactivacion=45)
        self.dispositivo("exacta-p", apagado=50, reactivacion=60)
        self.dispositivo("cerca-i", apagado=15, reactivacion=25)
        self.dispositivo("cerca-c", apagado=30, reactivacion=40)
        self.dispositivo("cerca-p", apagado=70, reactivacion=90)
        # Empate entre importante y confort (distancia 15 a cada una): protege más.
        self.dispositivo("empate", apagado=27.5, reactivacion=37.5)
        self.subir()

        filas = self.filas()
        esperado = {
            "bombillo": ("esencial", 40),
            "otro": ("esencial", 40),
            "sin-umbrales": ("confort", 20),
            "exacta-i": ("importante", 30),
            "exacta-c": ("confort", 20),
            "exacta-p": ("prescindible", 10),
            "cerca-i": ("importante", 30),
            "cerca-c": ("confort", 20),
            "cerca-p": ("prescindible", 10),
            "empate": ("importante", 30),
        }
        for nombre, (categoria, prioridad) in esperado.items():
            with self.subTest(nombre=nombre):
                self.assertEqual((filas[nombre].categoria, filas[nombre].prioridad), (categoria, prioridad))

        # Los umbrales no cambian, las columnas nuevas nacen con su valor por defecto.
        self.assertEqual((filas["cerca-i"].umbral_apagado_soc, filas["cerca-i"].umbral_reactivacion_soc), (15, 25))
        self.assertIsNone(filas["sin-umbrales"].umbral_apagado_soc)
        self.assertTrue(all(f.activo and f.ubicacion is None for f in filas.values()))

    def test_categoria_not_null_y_fk(self):
        self.subir()
        for categoria in ("NULL", "'inventada'"):
            with self.subTest(categoria=categoria), self.assertRaises(IntegrityError), self.engine.begin() as c:
                c.execute(text(f"INSERT INTO dispositivos (domo_id, tipo, nombre, estado, categoria) VALUES (1, 'toma', 'x', 'apagado', {categoria})"))

    def test_check_de_umbrales(self):
        self.subir()
        insertar = (
            "INSERT INTO dispositivos (domo_id, tipo, nombre, estado, categoria, umbral_apagado_soc, umbral_reactivacion_soc) "
            "VALUES (1, 'toma', 'x', 'apagado', 'confort', {}, {})"
        )
        for apagado, reactivacion in [
            ("-1", "40"),
            ("95", "101"),
            ("-10", "-5"),
            ("30", "NULL"),
            ("NULL", "40"),
            ("30", "34.9"),
            ("30", "30"),
            ("40", "30"),
            ("'NaN'", "'NaN'"),
        ]:
            with self.subTest(apagado=apagado, reactivacion=reactivacion):
                with self.assertRaises(IntegrityError), self.engine.begin() as c:
                    c.execute(text(insertar.format(apagado, reactivacion)))
        for apagado, reactivacion in [("NULL", "NULL"), ("30", "35"), ("0", "5"), ("95", "100")]:
            with self.subTest(apagado=apagado, reactivacion=reactivacion), self.engine.begin() as c:
                c.execute(text(insertar.format(apagado, reactivacion)))

    def test_banda_exacta_con_decimales_y_columnas_real(self):
        self.subir()
        with self.engine.connect() as c:
            tipo = c.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'dispositivos' AND column_name = 'umbral_apagado_soc'"
            )).scalar()
            self.assertEqual(tipo, "real")
            # Cada banda de exactamente 5 con dos decimales, de 0.00/5.00 a 95.00/100.00.
            insertados = c.execute(text(
                "INSERT INTO dispositivos (domo_id, tipo, nombre, estado, categoria, umbral_apagado_soc, umbral_reactivacion_soc) "
                "SELECT 1, 'toma', 'x', 'apagado', 'confort', g / 100.0, (g + 500) / 100.0 FROM generate_series(0, 9500) g"
            )).rowcount
            c.rollback()
        self.assertEqual(insertados, 9501)

    def test_ubicacion_de_100_caracteres(self):
        self.dispositivo("t")
        self.subir()
        actualizar = "UPDATE dispositivos SET ubicacion = :u"
        with self.engine.begin() as c:
            c.execute(text(actualizar), {"u": "x" * 100})
        with self.assertRaises(Exception), self.engine.begin() as c:
            c.execute(text(actualizar), {"u": "x" * 101})

    def test_una_fila_invalida_no_deja_nada_a_medias(self):
        # Reactivación > apagado cumple el CHECK viejo pero no la banda de 5.
        self.dispositivo("estrecha", apagado=30, reactivacion=31)
        resultado = alembic("upgrade", NUEVA)
        self.assertNotEqual(resultado.returncode, 0)
        self.assertEqual(self.version(), ANTERIOR)
        with self.engine.connect() as c:
            self.assertIsNone(c.execute(text("SELECT to_regclass('categoria_dispositivo')")).scalar())
            columnas = c.execute(text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'dispositivos' AND column_name = 'categoria'"
            )).scalar()
        self.assertEqual(columnas, 0)

    def test_rls_en_el_catalogo(self):
        self.subir()
        with self.engine.connect() as c:
            activo = c.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'categoria_dispositivo'")).scalar()
        self.assertTrue(activo)

    def test_downgrade_y_upgrade_de_nuevo(self):
        self.dispositivo("t", apagado=30, reactivacion=40, prioridad=7)
        self.dispositivo("sin", prioridad=None)
        with self.engine.connect() as c:
            antes = self._esquema(c)
        self.subir()
        resultado = alembic("downgrade", ANTERIOR)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(self.version(), ANTERIOR)
        with self.engine.connect() as c:
            self.assertEqual(self._esquema(c), antes)
            self.assertEqual(
                c.execute(text("SELECT umbral_apagado_soc, umbral_reactivacion_soc FROM dispositivos WHERE nombre = 't'")).one(),
                (30, 40),
            )
        self.subir()
        self.assertEqual(self.version(), NUEVA)

    @staticmethod
    def _esquema(conexion):
        """Columnas, restricciones y tablas de public, para comparar antes y después."""
        columnas = conexion.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
        )).all()
        restricciones = conexion.execute(text(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
        )).all()
        rls = conexion.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1"
        )).all()
        return columnas, restricciones, rls

    def test_el_codigo_orm_funciona_sobre_el_esquema_migrado(self):
        self.dispositivo("t")
        self.subir()
        codigo = (
            "import os; os.environ['JWT_SECRET_KEY'] = 'x'\n"
            "from database import SessionLocal\n"
            "from models import Dispositivo, Domo\n"
            "from servicios.control_energetico import _tomas_apagadas_por_sistema, evaluar_control_energetico, modo_actual_de\n"
            "from models import LecturaBateria\n"
            "db = SessionLocal()\n"
            "db.query(Dispositivo).update({'estado': 'encendido', 'categoria': 'confort', 'umbral_apagado_soc': 35, 'umbral_reactivacion_soc': 45})\n"
            "db.add(LecturaBateria(domo_id=1, estado_carga_soc=10)); db.commit()\n"
            "r = evaluar_control_energetico(db, 1)\n"
            "print([a.accion_realizada for a in r.acciones], modo_actual_de([t for t, _ in _tomas_apagadas_por_sistema(db, db.get(Domo, 1))]))\n"
        )
        resultado = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": URL, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("['apagar'] supervivencia", resultado.stdout)


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class MigracionDesactivacionYContrasena(TestCase):
    """e6c2a8d4f1b9: desactivado_en, debe_cambiar_contrasena y descripciones del catálogo."""

    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        vaciar(self.engine)
        self.assertEqual(alembic("upgrade", BASE_FASE_1).returncode, 0)
        with self.engine.begin() as c:
            for nombre, tabla, condicion in _modulo_migracion("b7d2c4e8a1f3_integridad_fase_1").CHECK_ANTIGUOS:
                c.execute(text(f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} CHECK ({condicion})"))
        resultado = alembic("upgrade", PREVIA_R6C)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO usuarios (id, nombre, correo, contrasena_hash, rol, estado_cuenta) "
                "VALUES (1, 'x', 'x@x.co', 'x', 'dueño', 'activo')"
            ))
            c.execute(text("INSERT INTO domos (id, propietario_id, nombre, estado_domo) VALUES (1, 1, 'd', 'activo')"))
            for id_, nombre, activo in ((1, "activa", True), (2, "Prueba Vatia", False), (3, "sin historial", False)):
                c.execute(text(
                    "INSERT INTO dispositivos (id, domo_id, tipo, nombre, estado, categoria, activo) "
                    "VALUES (:i, 1, 'toma', :n, 'apagado', 'confort', :a)"
                ), dict(i=id_, n=nombre, a=activo))
            for dispositivo, rol, accion, cuando in (
                (1, "sistema", "apagar", "2026-10-01 10:00+00"),
                (2, "dueño", "encender", "2026-10-01 08:00+00"),
                (2, "sistema", "apagar", "2026-10-01 10:00+00"),
            ):
                c.execute(text(
                    "INSERT INTO historial_acciones (domo_id, dispositivo_id, rol, accion_realizada, fecha_hora) "
                    "VALUES (1, :d, :r, :a, :f)"
                ), dict(d=dispositivo, r=rol, a=accion, f=cuando))

    def version(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()

    def descripciones(self):
        with self.engine.connect() as c:
            return dict(c.execute(text("SELECT codigo, descripcion FROM categoria_dispositivo")).all())

    def previa(self):
        with self.engine.connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            return c.execute(text(CONSULTA_PREVIA_R6C)).mappings().one()

    def test_la_consulta_previa(self):
        fila = self.previa()
        self.assertEqual(
            (fila["version_actual"], fila["version_correcta"], fila["sin_desactivado_en"], fila["sin_debe_cambiar_contrasena"]),
            (PREVIA_R6C, True, True, True),
        )
        self.assertEqual((fila["dispositivos_inactivos"], fila["inactivos_sin_historial"]), (2, 1))
        self.assertIn("Prueba Vatia", fila["relleno_previsto"])
        self.assertTrue(fila["listo_para_migrar"])
        self.assertEqual(alembic("upgrade", R6C).returncode, 0)
        fila = self.previa()
        self.assertEqual((fila["version_correcta"], fila["sin_desactivado_en"], fila["listo_para_migrar"]), (False, False, False))

    def test_relleno_columnas_y_descripciones(self):
        antes = self.descripciones()
        self.assertIn("15 puntos", antes["confort"])
        resultado = alembic("upgrade", R6C)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        with self.engine.connect() as c:
            fechas = dict(c.execute(text("SELECT nombre, desactivado_en FROM dispositivos")).all())
            ahora = c.execute(text("SELECT now()")).scalar()
            usuarios = c.execute(text("SELECT debe_cambiar_contrasena FROM usuarios")).scalars().all()
            c.execute(text(
                "INSERT INTO usuarios (id, nombre, correo, contrasena_hash, rol, estado_cuenta) "
                "VALUES (2, 'y', 'y@y.co', 'x', 'administrador', 'activo')"
            ))
            nuevo = c.execute(text("SELECT debe_cambiar_contrasena FROM usuarios WHERE id = 2")).scalar()
            c.rollback()
        self.assertIsNone(fechas["activa"])
        self.assertEqual(fechas["Prueba Vatia"].astimezone(timezone.utc).isoformat(), "2026-10-01T10:00:00+00:00")
        self.assertLess(abs((fechas["sin historial"] - ahora).total_seconds()), 120)
        self.assertEqual((usuarios, nuevo), ([False], False))
        despues = self.descripciones()
        self.assertEqual(set(despues), set(antes))
        for codigo, texto in despues.items():
            with self.subTest(codigo=codigo):
                self.assertNotIn("puntos", texto)
                self.assertNotEqual(texto, antes[codigo])

    def test_reporte_sobre_el_relleno(self):
        """El corte de Prueba Vatia, desactivada después, deja de crecer: 0 horas."""
        self.assertEqual(alembic("upgrade", "head").returncode, 0)
        codigo = (
            "import os; os.environ['JWT_SECRET_KEY'] = 'x'\n"
            "from datetime import date, datetime, timezone\n"
            "from database import SessionLocal\n"
            "from servicios.reportes import resumen_del_domo\n"
            "r = resumen_del_domo(SessionLocal(), 1, date(2026, 9, 30), date(2026, 10, 2), datetime(2026, 10, 2, 12, tzinfo=timezone.utc))\n"
            "print(sorted((t['nombre'], round(t['horas_apagada'], 3)) for t in r['cortes_por_toma']))\n"
        )
        resultado = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": URL, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("[('Prueba Vatia', 0.0), ('activa', 26.0)]", resultado.stdout)

    def test_downgrade_y_upgrade_de_nuevo(self):
        antes = self.descripciones()
        with self.engine.connect() as c:
            esquema = Migracion._esquema(c)
        self.assertEqual(alembic("upgrade", R6C).returncode, 0)
        resultado = alembic("downgrade", PREVIA_R6C)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(self.version(), PREVIA_R6C)
        self.assertEqual(self.descripciones(), antes)
        with self.engine.connect() as c:
            self.assertEqual(Migracion._esquema(c), esquema)
        self.assertEqual(alembic("upgrade", R6C).returncode, 0)
        self.assertEqual(self.version(), R6C)

    def test_domo_de_demostracion(self):
        """f7d3b9e5a2c8: la consulta previa, la columna y la vuelta atrás."""
        self.assertEqual(alembic("upgrade", R6C).returncode, 0)
        with self.engine.connect() as c:
            esquema = Migracion._esquema(c)
            c.execute(text("SET TRANSACTION READ ONLY"))
            fila = c.execute(text(CONSULTA_PREVIA_R7)).mappings().one()
        self.assertEqual(
            (fila["version_actual"], fila["version_correcta"], fila["sin_es_demostracion"], fila["domos"], fila["ya_hay_uno_con_el_nombre_demo"], fila["listo_para_migrar"]),
            (R6C, True, True, 1, 0, True),
        )
        resultado = alembic("upgrade", NUEVA)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        with self.engine.connect() as c:
            columna = c.execute(text(
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'domos' AND column_name = 'es_demostracion'"
            )).one()
            existentes = c.execute(text("SELECT es_demostracion FROM domos")).scalars().all()
            fila = c.execute(text(CONSULTA_PREVIA_R7)).mappings().one()
        self.assertEqual(tuple(columna), ("boolean", "NO", "false"))
        self.assertEqual(existentes, [False])
        self.assertEqual((fila["version_correcta"], fila["sin_es_demostracion"], fila["listo_para_migrar"]), (False, False, False))
        resultado = alembic("downgrade", R6C)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        with self.engine.connect() as c:
            self.assertEqual(Migracion._esquema(c), esquema)
        self.assertEqual(alembic("upgrade", NUEVA).returncode, 0)
        self.assertEqual(self.version(), NUEVA)


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class TelemetriaEnColumnasReal(TestCase, Comprobaciones, ComprobacionesExportacion):
    """Con todas las columnas numéricas como `real`, que es su tipo en Supabase."""

    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)
        vaciar(cls.engine)
        assert alembic("upgrade", BASE_FASE_1).returncode == 0
        with cls.engine.begin() as c:
            for nombre, tabla, condicion in _modulo_migracion("b7d2c4e8a1f3_integridad_fase_1").CHECK_ANTIGUOS:
                c.execute(text(f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} CHECK ({condicion})"))
            columnas = c.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type = 'double precision'"
            )).all()
            assert columnas
            for tabla, columna in columnas:
                c.execute(text(f"ALTER TABLE {tabla} ALTER COLUMN {columna} TYPE real"))
            indices_de_supabase(c)
        resultado = alembic("upgrade", "head")
        assert resultado.returncode == 0, resultado.stderr
        with cls.engine.begin() as c:
            c.execute(text(
                "INSERT INTO usuarios (id, nombre, correo, contrasena_hash, rol, estado_cuenta) "
                "VALUES (1, 'x', 'x@x.co', 'x', 'dueño', 'activo')"
            ))
            c.execute(text("INSERT INTO domos (id, propietario_id, nombre, estado_domo) VALUES (1, 1, 'd', 'activo')"))
            c.execute(text("SELECT setval(pg_get_serial_sequence('domos', 'id'), 1)"))
        cls.Sesion = sessionmaker(bind=cls.engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        def _get_db():
            db = self.Sesion()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _get_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, raise_server_exceptions=False)
        with self.Sesion() as db:
            self.cabeceras = {"Authorization": f"Bearer {crear_token_acceso(db.get(Usuario, 1))}"}

    def test_es_real_de_verdad_y_sin_limite_la_base_lo_rechazaria(self):
        with self.engine.connect() as c:
            with self.assertRaises(Exception):
                c.execute(text("INSERT INTO lecturas_bateria (domo_id, voltaje) VALUES (1, 1e300)"))
            c.rollback()
            with self.assertRaises(Exception):
                c.execute(text("INSERT INTO lecturas_bateria (domo_id, voltaje) VALUES (1, 1e-300)"))
            c.rollback()

    def test_api_1e300_da_422_y_los_limites_caben(self):
        for ruta, campos in CAMPOS_POR_RUTA.items():
            for campo in campos:
                minimo, maximo = LIMITES_TELEMETRIA[campo]
                with self.subTest(ruta=ruta, campo=campo):
                    url = f"/domos/1{ruta}"
                    self.assertEqual(self.client.post(url, json={campo: 1e300}, headers=self.cabeceras).status_code, 422)
                    for valor in (minimo, maximo, 12.345):
                        if minimo <= valor <= maximo:
                            respuesta = self.client.post(url, json={campo: valor}, headers=self.cabeceras)
                            self.assertEqual(respuesta.status_code, 201, respuesta.text)

    def test_api_diminuto_no_da_500(self):
        respuesta = self.client.post("/domos/1/lecturas-bateria", json={"corriente": 1e-300}, headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 201, respuesta.text)
        self.assertEqual(respuesta.json()["corriente"], 0)

    def test_alerta_con_1e300_da_422(self):
        cuerpo = {"tipo_alerta": "sobrecarga", "valor_detectado": 1e300}
        self.assertEqual(self.client.post("/domos/1/alertas", json=cuerpo, headers=self.cabeceras).status_code, 422)

    def _domo_nuevo(self):
        with self.engine.begin() as c:
            return c.execute(text(
                "INSERT INTO domos (propietario_id, nombre, estado_domo) VALUES (1, 'reportes', 'activo') RETURNING id"
            )).scalar()

    def test_reporte_en_postgres_con_columnas_real_y_hora_de_bogota(self):
        domo_id = self._domo_nuevo()
        with self.Sesion() as db:
            escenario(db, domo_id)
        respuesta = self.client.get(f"/domos/{domo_id}/reportes/resumen", params={"desde": DIA_1, "hasta": DIA_2}, headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.comprobar_escenario(respuesta.json())

    def test_exportacion_en_postgres(self):
        domo_id = self._domo_nuevo()
        with self.Sesion() as db:
            escenario(db, domo_id)
        with self.Sesion() as db:
            self.comprobar_exportacion(db, domo_id)

    def test_exportacion_en_transaccion_de_solo_lectura(self):
        with self.Sesion() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            with tempfile.TemporaryDirectory() as temporal:
                _cargar_exportador().exportar(db, Path(temporal), [1])
            with self.assertRaises(Exception):
                db.execute(text("UPDATE domos SET nombre = 'x' WHERE id = 1"))

    def test_columnas_de_la_migracion(self):
        with self.engine.connect() as c:
            filas = c.execute(text(
                "SELECT table_name, column_name, is_nullable, column_default FROM information_schema.columns "
                "WHERE (table_name, column_name) IN (('usuarios', 'version_sesion'), ('alertas', 'fecha_atendida')) ORDER BY 1"
            )).all()
        self.assertEqual([(f[0], f[1], f[2]) for f in filas], [("alertas", "fecha_atendida", "YES"), ("usuarios", "version_sesion", "NO")])
        self.assertEqual(filas[1][3], "0")
        with self.engine.connect() as c:
            filas = c.execute(text(
                "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE (table_name, column_name) IN (('dispositivos', 'desactivado_en'), ('usuarios', 'debe_cambiar_contrasena')) ORDER BY 1"
            )).all()
        self.assertEqual(
            [tuple(f) for f in filas],
            [("dispositivos", "desactivado_en", "timestamp with time zone", "YES", None), ("usuarios", "debe_cambiar_contrasena", "boolean", "NO", "false")],
        )

    def test_generador_de_demostracion_en_postgres(self):
        """30 días con columnas `real`: escribe, el reporte y la exportación los leen, y
        --reiniciar los regenera iguales sin tocar el domo 1."""
        from tests.test_demo import FIN, g

        def filas_domo_1():
            with self.Sesion() as db:
                return g.filas_del_domo(db, 1)

        antes = filas_domo_1()
        dichos = []
        with self.Sesion() as db:
            self.assertTrue(g.ejecutar(db, "x@x.co", g.Parametros(), preguntar=lambda _: "s", decir=dichos.append, fin=FIN))
        sim = g.simular(g.Parametros(), FIN)
        with self.Sesion() as db:
            domo = db.query(Domo).filter(Domo.nombre == g.NOMBRE_DEMO).one()
            self.assertTrue(domo.es_demostracion)
            filas = g.filas_del_domo(db, domo.id)
            socs = [s for (s,) in db.query(LecturaBateria.estado_carga_soc).filter(LecturaBateria.domo_id == domo.id).order_by(LecturaBateria.fecha_hora)]
            primera = db.query(LecturaBateria.fecha_hora).filter(LecturaBateria.domo_id == domo.id).order_by(LecturaBateria.fecha_hora).first()[0]
        self.assertEqual(filas["lecturas_bateria"], 8640)
        self.assertEqual((filas["historial_acciones"], filas["alertas"], filas["dispositivos"]), (len(sim.acciones), len(sim.alertas), 4))
        self.assertEqual([round(x, 3) for x in socs], [round(f["estado_carga_soc"], 3) for f in sim.lecturas_bateria])
        self.assertEqual(primera, FIN - timedelta(days=30) + timedelta(minutes=5))

        hoy = FIN.astimezone(g.ZONA_BOGOTA).date()
        respuesta = self.client.get(f"/domos/{domo.id}/reportes/resumen", params={"desde": hoy - timedelta(days=29), "hasta": hoy}, headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        reporte = respuesta.json()
        self.assertTrue(reporte["es_demostracion"])
        kwh = sum(f["energia_generada"] for f in sim.lecturas_fotovoltaicas) / 1000
        self.assertAlmostEqual(reporte["kwh_generados"], kwh, delta=kwh * 0.02)
        # Los datos empiezan 30 días × 24 h antes; el rango de 30 días de Reportes, a la
        # medianoche siguiente: los cortes de esa primera tarde quedan fuera.
        desde = datetime.combine(hoy - timedelta(days=29), datetime.min.time(), g.ZONA_BOGOTA)
        sistema = [a for a in sim.acciones if a["rol"] == "sistema" and a["accion_realizada"] == "apagar" and a["fecha_hora"] >= desde]
        self.assertEqual(sum(c["cortes"] for c in reporte["cortes_por_categoria"]), len(sistema))
        with self.Sesion() as db, tempfile.TemporaryDirectory() as temporal:
            db.execute(text("SET TRANSACTION READ ONLY"))
            _cargar_exportador().exportar(db, Path(temporal), [domo.id])
            dim = (Path(temporal) / "dim_domo.csv").read_text(encoding="utf-8-sig")
        self.assertIn("Demostración · datos sintéticos", dim)

        with self.Sesion() as db:
            self.assertTrue(g.ejecutar(db, "x@x.co", g.Parametros(), reiniciar=True, preguntar=lambda _: "s", decir=dichos.append, fin=FIN))
        with self.Sesion() as db:
            self.assertEqual(g.filas_del_domo(db, domo.id), filas)
            otra = [s for (s,) in db.query(LecturaBateria.estado_carga_soc).filter(LecturaBateria.domo_id == domo.id).order_by(LecturaBateria.fecha_hora)]
            self.assertEqual(db.query(Domo).filter(Domo.nombre == g.NOMBRE_DEMO).count(), 1)
        self.assertEqual(otra, socs)
        self.assertEqual(filas_domo_1(), antes)

    def test_listener_guarda_null_y_conserva_la_lectura(self):
        listener = importar_listener()
        listener._domos_conocidos.clear()
        modelo, permitidos = listener.MAPEO_TIPOS["bateria"]
        with patch.object(listener, "SesionListener", self.Sesion):
            with self.assertLogs("mqtt_listener", "WARNING"):
                guardada = listener._insertar_lectura(
                    1, "bateria", modelo, permitidos,
                    {"voltaje": 1e300, "corriente": 1e-300, "temperatura": 1e39, "estado_carga_soc": 80},
                )
        self.assertTrue(guardada)
        with self.Sesion() as db:
            fila = db.query(LecturaBateria).order_by(LecturaBateria.id.desc()).first()
        self.assertEqual((fila.voltaje, fila.corriente, fila.temperatura, fila.estado_carga_soc), (None, 0, None, 80))
