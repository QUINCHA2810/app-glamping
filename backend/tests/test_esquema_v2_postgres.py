"""
Las revisiones de alembic_v2, tools/copiar_a_v2.py y la API del modelo v2 contra un
Postgres de verdad, vacío y desechable. Reemplazan, para v2, a las pruebas de API sobre
Postgres de tests/test_migracion_postgres.py (que prueba la línea v1).

Se omite si TEST_POSTGRES_URL no está definida, y se niega a correr si no apunta a esta
máquina: borra los esquemas public y bi, igual que tests/test_migracion_postgres.py.

    TEST_POSTGRES_URL=postgresql://postgres:clave@localhost:5433/postgres python -m unittest tests.test_esquema_v2_postgres
"""

import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlparse

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Domo, Medicion, Usuario
from schemas import LIMITES_TELEMETRIA
from security import crear_token_acceso
from servicios.mediciones import guardar_medicion
from tests.base import importar_listener
from tests.test_copiar_a_v2 import FECHA_RESPALDO, copiar, migracion_v2, respaldo_base
from tests.test_reportes import DIA_1, DIA_2, Comprobaciones, ComprobacionesExportacion, _cargar_exportador, escenario
from tests.test_telemetria_y_globales import CAMPOS_POR_RUTA, CUERPO_BASE

URL = os.environ.get("TEST_POSTGRES_URL")
BACKEND = Path(__file__).resolve().parent.parent


LIMPIAR_ROL = """
DO $$
BEGIN
    -- lector_bi es del servidor, no del esquema: sobrevive al DROP SCHEMA.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_bi') THEN
        EXECUTE 'DROP OWNED BY lector_bi';
        EXECUTE 'DROP ROLE lector_bi';
    END IF;
END
$$
"""


def base_vacia(engine):
    """Borra bi, public y lector_bi: la base queda como un proyecto de Supabase nuevo."""
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS bi CASCADE"))
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        c.execute(text(LIMPIAR_ROL))


def alembic_v2(*argumentos):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_v2.ini", *argumentos],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL_V2": URL, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class EsquemaV2(TestCase):
    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        base_vacia(self.engine)
        self.correr("upgrade", "head")

    def correr(self, *argumentos):
        resultado = alembic_v2(*argumentos)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    # Datos mínimos: un dueño con un domo, una toma con una acción y un bombillo.
    def sembrar(self, c):
        c.execute(text(
            "INSERT INTO usuarios (nombre, correo, contrasena_hash) VALUES ('a', 'a@x.co', 'x'), ('b', 'b@x.co', 'x')"
        ))
        c.execute(text("INSERT INTO domos (nombre) VALUES ('d')"))
        c.execute(text("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (1, 1, 'dueno')"))
        c.execute(text(
            "INSERT INTO dispositivos (domo_id, nombre, tipo, categoria) "
            "VALUES (1, 'toma', 'toma', 'confort'), (1, 'luz', 'bombillo', 'esencial')"
        ))
        c.execute(text(
            "INSERT INTO acciones (dispositivo_id, accion, estado_anterior, estado_nuevo, actor_tipo, actor_ref, resultado) "
            "VALUES (1, 'encender', 'apagado', 'encendido', 'dueno', 'a@x.co', 'ok')"
        ))

    def rechaza(self, sql: str, error=IntegrityError):
        with self.engine.connect() as c:
            with c.begin():
                self.sembrar(c)
            with self.assertRaises(error, msg=sql):
                with c.begin():
                    c.execute(text(sql))

    def test_baja_y_vuelve_a_subir(self):
        self.correr("downgrade", "base")
        with self.engine.connect() as c:
            for tabla in migracion_v2.TABLAS:
                self.assertIsNone(c.execute(text(f"SELECT to_regclass('public.{tabla}')")).scalar(), tabla)
            # El downgrade de la tercera revisión quita bi y lector_bi (el lunes se vuelve a crear).
            self.assertIsNone(c.execute(text("SELECT to_regnamespace('bi')")).scalar())
            self.assertFalse(c.execute(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_bi')")).scalar())
        self.correr("upgrade", "head")
        with self.engine.connect() as c:
            rls = dict(c.execute(text(
                "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )).all())
        # clima es de la revisión 5a1c7e3f9b24, con su RLS como las demás.
        self.assertEqual(set(rls), {*migracion_v2.TABLAS, "clima", "alembic_version"})
        self.assertTrue(all(rls.values()), rls)

    def test_se_niega_sobre_una_base_v1(self):
        self.correr("downgrade", "base")
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE historial_acciones (id bigint)"))
        resultado = alembic_v2("upgrade", "head")
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("tablas del modelo v1", resultado.stderr)

    def test_segundo_dueno_en_un_domo(self):
        self.rechaza("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (2, 1, 'dueno')")

    def test_brillo_en_una_toma(self):
        self.rechaza("UPDATE dispositivos SET brillo = 50 WHERE id = 1")

    def test_bombillo_no_esencial(self):
        self.rechaza("UPDATE dispositivos SET categoria = 'confort' WHERE id = 2")

    def test_borrar_un_dispositivo_con_acciones(self):
        self.rechaza("DELETE FROM dispositivos WHERE id = 1")

    def test_alerta_atendida_sin_fecha(self):
        self.rechaza(
            "INSERT INTO alertas (domo_id, tipo, nivel, estado, mensaje) "
            "VALUES (1, 'sobrecarga', 'critico', 'atendida', 'm')"
        )

    def test_soc_fuera_de_0_a_100(self):
        for soc in ("100.01", "-0.5", "'NaN'", "'Infinity'"):
            with self.subTest(soc=soc):
                self.setUp()
                self.rechaza(f"INSERT INTO mediciones (domo_id, fecha_hora, soc_pct) VALUES (1, now(), {soc})")

    def test_lo_valido_entra(self):
        with self.engine.begin() as c:
            self.sembrar(c)
            c.execute(text("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (2, 1, 'administrador')"))
            c.execute(text("UPDATE dispositivos SET brillo = 40 WHERE id = 2"))
            c.execute(text(
                "INSERT INTO alertas (domo_id, tipo, nivel, estado, mensaje, fecha_atendida) "
                "VALUES (1, 'sobrecarga', 'critico', 'atendida', 'm', now())"
            ))
            c.execute(text("INSERT INTO mediciones (domo_id, fecha_hora, soc_pct, potencia_fv_w) VALUES (1, now(), 100, -100)"))

    def test_una_medicion_por_domo_y_bloque(self):
        self.rechaza(
            "INSERT INTO mediciones (domo_id, fecha_hora) VALUES (1, '2026-11-13T12:00:00Z'), (1, '2026-11-13T12:00:00Z')"
        )

    def test_la_copia_conserva_los_id_y_ajusta_las_secuencias(self):
        resultado = copiar.transformar(respaldo_base(), FECHA_RESPALDO)
        with self.engine.begin() as c:
            copiar.escribir(c, resultado)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text("SELECT id FROM dispositivos ORDER BY id")).scalars().all(), [10, 11])
            self.assertEqual(c.execute(text("SELECT id FROM asignaciones ORDER BY id")).scalars().all(), [5, 6])
            nuevo = c.execute(text(
                "INSERT INTO usuarios (nombre, correo, contrasena_hash) VALUES ('c', 'c@x.co', 'x') RETURNING id"
            )).scalar()
            self.assertEqual(nuevo, 3)
            self.assertEqual(c.execute(text("SELECT count(*) FROM mediciones")).scalar(), 1)
            actor = c.execute(text("SELECT actor_ref FROM acciones WHERE id = 100")).scalar()
            self.assertEqual(actor, "R-13")

    def test_la_copia_es_todo_o_nada(self):
        resultado = copiar.transformar(respaldo_base(), FECHA_RESPALDO)
        resultado.filas["alertas"][0]["tipo"] = "no_existe"  # falla a mitad de la copia
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as c:
                copiar.escribir(c, resultado)
        with self.engine.connect() as c:
            for tabla in migracion_v2.TABLAS:
                self.assertEqual(c.execute(text(f"SELECT count(*) FROM {tabla}")).scalar(), 0, tabla)

    def test_la_copia_se_niega_si_el_destino_no_esta_vacio(self):
        with self.engine.begin() as c:
            self.sembrar(c)
        with self.assertRaises(copiar.DestinoInvalido):
            with self.engine.begin() as c:
                copiar.escribir(c, copiar.transformar(respaldo_base(), FECHA_RESPALDO))


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class ApiSobrePostgres(TestCase, Comprobaciones, ComprobacionesExportacion):
    """La API, los reportes, la exportación, el generador y el listener sobre el esquema
    v2 real: aquí corren el INSERT … ON CONFLICT de Postgres y las fechas de Bogotá."""

    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)
        base_vacia(cls.engine)
        resultado = alembic_v2("upgrade", "head")
        assert resultado.returncode == 0, resultado.stderr
        with cls.engine.begin() as c:
            c.execute(text("INSERT INTO usuarios (nombre, correo, contrasena_hash) VALUES ('x', 'x@x.co', 'x')"))
            c.execute(text("INSERT INTO domos (nombre) VALUES ('d')"))
            c.execute(text("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (1, 1, 'dueno')"))
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

    def _domo_nuevo(self):
        with self.engine.begin() as c:
            domo_id = c.execute(text("INSERT INTO domos (nombre) VALUES ('reportes') RETURNING id")).scalar()
            c.execute(text("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (1, :d, 'dueno')"), {"d": domo_id})
        return domo_id

    def test_api_1e300_da_422_y_los_limites_caben(self):
        domo_id = self._domo_nuevo()
        for ruta, campos in CAMPOS_POR_RUTA.items():
            for campo in campos:
                minimo, maximo = LIMITES_TELEMETRIA[campo]
                with self.subTest(ruta=ruta, campo=campo):
                    url = f"/domos/{domo_id}{ruta}"
                    base = CUERPO_BASE.get(ruta, {})
                    self.assertEqual(self.client.post(url, json={**base, campo: 1e300}, headers=self.cabeceras).status_code, 422)
                    for valor in (minimo, maximo, 12.345):
                        respuesta = self.client.post(url, json={**base, campo: valor}, headers=self.cabeceras)
                        self.assertEqual(respuesta.status_code, 201, respuesta.text)

    def test_la_energia_se_suma_en_el_bloque_y_lo_instantaneo_se_reemplaza(self):
        domo_id = self._domo_nuevo()
        instante = FECHA_RESPALDO
        with self.Sesion() as db:
            guardar_medicion(db, domo_id, "consumo", {"potencia_consumida": 60, "energia_consumida": 0.1}, instante)
            guardar_medicion(db, domo_id, "bateria", {"estado_carga_soc": 70}, instante)
            guardar_medicion(db, domo_id, "consumo", {"potencia_consumida": 80, "energia_consumida": 0.2}, instante + timedelta(seconds=5))
            guardar_medicion(db, domo_id, "consumo", {"potencia_consumida": 90}, instante + timedelta(seconds=8))
            db.commit()
            (fila,) = db.query(Medicion).filter(Medicion.domo_id == domo_id).all()
        self.assertEqual((fila.potencia_consumo_w, fila.soc_pct), (90, 70))
        self.assertAlmostEqual(fila.energia_consumo_wh, 0.3)

    def test_r13_sobre_postgres(self):
        domo_id = self._domo_nuevo()
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO dispositivos (domo_id, nombre, tipo, categoria, estado) VALUES (:d, 't', 'toma', 'confort', 'encendido')"
            ), {"d": domo_id})
        self.client.post(f"/domos/{domo_id}/lecturas-bateria", json={"estado_carga_soc": 30}, headers=self.cabeceras)
        respuesta = self.client.post(f"/domos/{domo_id}/control-energetico/evaluar", headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual([(a["nombre"], a["accion_realizada"]) for a in respuesta.json()["acciones"]], [("t", "apagar")])
        historial = self.client.get(f"/domos/{domo_id}/historial", headers=self.cabeceras).json()
        self.assertEqual([h["rol"] for h in historial], ["sistema"])

    def test_reporte_en_postgres_con_hora_de_bogota(self):
        domo_id = self._domo_nuevo()
        with self.Sesion() as db:
            escenario(db, domo_id)
        respuesta = self.client.get(f"/domos/{domo_id}/reportes/resumen", params={"desde": DIA_1, "hasta": DIA_2}, headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.comprobar_escenario(respuesta.json())

    def test_exportacion_en_postgres_y_en_solo_lectura(self):
        domo_id = self._domo_nuevo()
        with self.Sesion() as db:
            escenario(db, domo_id)
        with self.Sesion() as db:
            self.comprobar_exportacion(db, domo_id)
        with self.Sesion() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            with tempfile.TemporaryDirectory() as temporal:
                _cargar_exportador().exportar(db, Path(temporal), [domo_id])
            with self.assertRaises(Exception):
                db.execute(text("UPDATE domos SET nombre = 'x' WHERE id = 1"))

    def test_generador_de_demostracion_en_postgres(self):
        from tests.test_demo import FIN, g

        dichos = []
        with self.Sesion() as db:
            self.assertTrue(g.ejecutar(db, "x@x.co", g.Parametros(dias=3), preguntar=lambda _: "s", decir=dichos.append, fin=FIN))
        sim = g.simular(g.Parametros(dias=3), FIN)
        with self.Sesion() as db:
            domo = db.query(Domo).filter(Domo.nombre == g.NOMBRE_DEMO).one()
            filas = g.filas_del_domo(db, domo.id)
        self.assertEqual(filas, {"mediciones": 3 * 288, "acciones": len(sim.acciones), "alertas": len(sim.alertas), "dispositivos": 4})
        # Con --reiniciar se borran las acciones antes que los dispositivos: la base no deja al revés.
        with self.Sesion() as db:
            self.assertTrue(g.ejecutar(db, "x@x.co", g.Parametros(dias=3), reiniciar=True, preguntar=lambda _: "s", decir=dichos.append, fin=FIN))
        with self.Sesion() as db:
            self.assertEqual(g.filas_del_domo(db, domo.id), filas)

    def test_listener_guarda_null_y_conserva_la_lectura(self):
        domo_id = self._domo_nuevo()
        listener = importar_listener()
        listener._domos_conocidos.clear()
        campos, aceptados = listener.MAPEO_TIPOS["bateria"]
        with patch.object(listener, "SesionListener", self.Sesion):
            with self.assertLogs("mqtt_listener", "WARNING"):
                guardada = listener._insertar_lectura(
                    domo_id, "bateria", campos, aceptados,
                    {"voltaje": 1e300, "corriente": 1e-300, "temperatura": 1e39, "estado_carga_soc": 80},
                )
        self.assertTrue(guardada)
        with self.Sesion() as db:
            fila = db.query(Medicion).filter(Medicion.domo_id == domo_id).one()
        self.assertEqual(
            (fila.voltaje_bateria_v, fila.corriente_bateria_a, fila.temperatura_bateria_c, fila.soc_pct), (None, 0, None, 80)
        )


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class BiSobrePostgres(TestCase):
    """El esquema bi de v2 (revisión 4e8b2c6d9f17): mismas columnas que v1, mismas reglas
    que la app y el mismo mínimo privilegio de lector_bi."""

    @classmethod
    def setUpClass(cls):
        if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra el esquema public.")
        cls.engine = create_engine(URL)
        base_vacia(cls.engine)
        resultado = alembic_v2("upgrade", "head")
        assert resultado.returncode == 0, resultado.stderr
        cls.Sesion = sessionmaker(bind=cls.engine, autoflush=False, expire_on_commit=False)
        cls.sembrar()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @classmethod
    def sembrar(cls):
        with cls.engine.begin() as c:
            c.execute(text("INSERT INTO usuarios (nombre, correo, contrasena_hash) VALUES ('x', 'x@x.co', 'x')"))
            cls.domo_id = c.execute(text(
                "INSERT INTO domos (nombre, bateria_tipo, bateria_capacidad_wh, perfil) "
                "VALUES ('bi', 'agm', 1200, 'equilibrado') RETURNING id"
            )).scalar()
            c.execute(text("INSERT INTO asignaciones (usuario_id, domo_id, rol) VALUES (1, :d, 'dueno')"), {"d": cls.domo_id})
            ids = {}
            for nombre, tipo, categoria, estado, id_fisico in (
                ("imp", "toma", "importante", "encendido", None),
                ("conf", "toma", "confort", "apagado", "toma1"),
                ("pres", "toma", "prescindible", "apagado", None),
                ("luz", "bombillo", "esencial", "encendido", None),
            ):
                ids[nombre] = c.execute(text(
                    "INSERT INTO dispositivos (domo_id, nombre, tipo, categoria, estado, id_fisico) "
                    "VALUES (:d, :n, :t, :c, :e, :f) RETURNING id"
                ), {"d": cls.domo_id, "n": nombre, "t": tipo, "c": categoria, "e": estado, "f": id_fisico}).scalar()
            for dispositivo, accion, actor, minutos in (
                ("conf", "encender", "dueno", 50),
                ("conf", "apagar", "sistema", 40),
                ("pres", "apagar", "sistema", 30),
                ("imp", "encender", "administrador", 20),
            ):
                c.execute(text(
                    "INSERT INTO acciones (dispositivo_id, accion, estado_anterior, estado_nuevo, actor_tipo, actor_ref, "
                    "resultado, fecha_hora) VALUES (:i, :a, 'apagado', 'encendido', :r, 'ref', 'ok', "
                    "now() - make_interval(mins => :m))"
                ), {"i": ids[dispositivo], "a": accion, "r": actor, "m": minutos})
            c.execute(text(
                "INSERT INTO alertas (domo_id, tipo, nivel, estado, variable, valor_detectado, limite, mensaje, fecha_hora, "
                "fecha_atendida) VALUES (:d, 'bateria_baja', 'advertencia', 'atendida', 'estado_carga_soc', 55, 60, 'm', "
                "now() - interval '30 minutes', now())"
            ), {"d": cls.domo_id})
            cls.ahora = c.execute(text("SELECT now()")).scalar()
        cls.ids = ids
        inicio = cls.ahora - timedelta(hours=1)
        with cls.Sesion() as db:
            for i in range(60):
                t = inicio + timedelta(seconds=10 * i)
                guardar_medicion(db, cls.domo_id, "fotovoltaica", {"potencia_generada": 100.0 + i, "energia_generada": 0.3}, t)
                guardar_medicion(db, cls.domo_id, "consumo", {"potencia_consumida": 50.0, "energia_consumida": 0.14}, t)
                guardar_medicion(db, cls.domo_id, "bateria", {"estado_carga_soc": 60.0 - i * 0.1}, t)
            # Un hueco de 20 minutos (no se integra) y más lecturas.
            for i in range(5):
                t = inicio + timedelta(minutes=30, seconds=10 * i)
                guardar_medicion(db, cls.domo_id, "fotovoltaica", {"potencia_generada": 200.0}, t)
                guardar_medicion(db, cls.domo_id, "sensores", {"variable_medida": "frecuencia", "valor": 60.0}, t)
                guardar_medicion(db, cls.domo_id, "sensores", {"variable_medida": "factor_potencia", "valor": 0.9}, t)
            # Lo último: una lectura de batería en null (ESP32 sin sensor).
            guardar_medicion(db, cls.domo_id, "bateria", {"estado_carga_soc": None}, inicio + timedelta(minutes=40))
            db.commit()

    def filas(self, sql, **parametros):
        with self.engine.connect() as c:
            return c.execute(text(sql), {"d": self.domo_id, **parametros}).mappings().all()

    def test_mismas_columnas_que_v1(self):
        from tests.test_bi_v2 import COLUMNAS_V1

        for vista, columnas in COLUMNAS_V1.items():
            with self.subTest(vista=vista):
                reales = [f["column_name"] for f in self.filas(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'bi' AND table_name = :v "
                    "ORDER BY ordinal_position", v=vista
                )]
                self.assertEqual(tuple(reales), columnas)

    def test_umbrales_y_prioridad_calculados_como_la_app(self):
        from servicios.categorias import preset_de

        for fila in self.filas("SELECT * FROM bi.v_dispositivos WHERE domo_id = :d"):
            with self.subTest(dispositivo=fila["nombre"]):
                preset = preset_de(fila["categoria"], "agm", "equilibrado")
                self.assertEqual(fila["prioridad"], preset.prioridad)
                apagado, reactivacion = fila["umbral_apagado_soc"], fila["umbral_reactivacion_soc"]
                self.assertEqual(None if apagado is None else float(apagado), preset.umbral_apagado_soc)
                self.assertEqual(None if reactivacion is None else float(reactivacion), preset.umbral_reactivacion_soc)
                self.assertEqual(fila["real"], fila["nombre"] == "conf")

    def test_domo(self):
        (fila,) = self.filas("SELECT * FROM bi.v_domos WHERE domo_id = :d")
        self.assertEqual((fila["tipo_bateria"], float(fila["reserva_soc"]), fila["perfil_control"]), ("agm", 50.0, "equilibrado"))
        self.assertEqual(float(fila["capacidad_wh"]), 1200.0)
        self.assertEqual(fila["origen_datos"], "Domo del proyecto")

    def test_acciones_con_el_rol_de_v1(self):
        filas = self.filas("SELECT rol, automatica FROM bi.v_acciones WHERE domo_id = :d ORDER BY momento_local")
        self.assertEqual([f["rol"] for f in filas], ["dueño", "sistema", "sistema", "administrador"])
        self.assertEqual([f["automatica"] for f in filas], [False, True, True, False])

    def test_estado_actual(self):
        (fila,) = self.filas("SELECT * FROM bi.v_estado_actual WHERE domo_id = :d")
        # El último SoC con valor (la lectura en null no lo borra); su fecha dice lo viejo que es.
        self.assertAlmostEqual(float(fila["soc"]), 60.0 - 59 * 0.1, places=6)
        self.assertEqual(float(fila["generacion_w"]), 200.0)
        self.assertEqual(float(fila["consumo_w"]), 50.0)
        # Cortadas por R-13: la confort y la prescindible.
        self.assertEqual((fila["estado_energetico"], fila["tomas_cortadas"]), ("supervivencia", 2))
        self.assertGreaterEqual(fila["minutos_desde_ultima_lectura"], 0)

    def test_energia_igual_a_reportes(self):
        from servicios.reportes import _kwh_por_dia

        with self.Sesion() as db:
            for campo, columna in (("potencia_fv_w", "generacion_wh"), ("potencia_consumo_w", "consumo_wh")):
                esperado = sum(_kwh_por_dia(db, Medicion, campo, self.domo_id, None, None).values()) * 1000.0
                (fila,) = self.filas(f"SELECT sum({columna}) AS wh FROM bi.v_lecturas_5min WHERE domo_id = :d")
                with self.subTest(medida=columna):
                    self.assertGreater(esperado, 0)
                    self.assertAlmostEqual(float(fila["wh"]), esperado, delta=0.01)

    def test_en_vivo_y_pzem(self):
        minutos = self.filas("SELECT * FROM bi.v_lecturas_minuto WHERE domo_id = :d")
        self.assertTrue(minutos)
        self.assertTrue(all(f["soc"] is None or 0 <= f["soc"] <= 100 for f in minutos))
        pzem = self.filas("SELECT frecuencia_hz, factor_potencia FROM bi.v_pzem WHERE domo_id = :d")
        self.assertTrue(pzem)
        self.assertEqual({(float(f["frecuencia_hz"]), float(f["factor_potencia"])) for f in pzem}, {(60.0, 0.9)})

    def test_alertas_y_cortes(self):
        (alerta,) = self.filas("SELECT * FROM bi.v_alertas WHERE domo_id = :d")
        self.assertEqual(
            (alerta["tipo_alerta"], alerta["nivel_prioridad"], alerta["estado_alerta"], alerta["variable_asociada"]),
            ("bateria_baja", "advertencia", "atendida", "estado_carga_soc"),
        )
        self.assertEqual((alerta["valor_detectado"], alerta["valor_limite"]), (55, 60))
        self.assertGreater(alerta["minutos_hasta_atendida"], 29)
        cortes = self.filas(
            "SELECT categoria, vigente, horas_apagada FROM bi.v_cortes WHERE domo_id = :d ORDER BY inicio_local"
        )
        self.assertEqual([(f["categoria"], f["vigente"]) for f in cortes], [("confort", True), ("prescindible", True)])
        self.assertTrue(all(f["horas_apagada"] > 0 for f in cortes))

    def test_lector_bi_minimo_privilegio(self):
        (rol,) = self.filas("SELECT rolcanlogin, rolconfig FROM pg_roles WHERE rolname = 'lector_bi'")
        self.assertFalse(rol["rolcanlogin"])
        self.assertIn("default_transaction_read_only=on", rol["rolconfig"])
        self.assertIn("statement_timeout=30s", rol["rolconfig"])
        (publico,) = self.filas(
            "SELECT has_schema_privilege('public', 'bi', 'USAGE') AS esquema, "
            "has_table_privilege('public', 'bi.v_domos', 'SELECT') AS vista"
        )
        self.assertEqual((publico["esquema"], publico["vista"]), (False, False))
        with self.engine.connect() as c:
            c.execute(text("SET ROLE lector_bi"))
            for vista in ("v_domos", "v_estado_actual", "v_lecturas_minuto", "v_lecturas_5min", "v_dispositivos",
                          "v_acciones", "v_alertas", "v_cortes", "v_pzem"):
                c.execute(text(f"SELECT count(*) FROM bi.{vista}")).scalar()
            c.rollback()
        for sql in ("SELECT count(*) FROM public.usuarios", "SELECT count(*) FROM public.mediciones"):
            with self.subTest(sql=sql), self.engine.connect() as c:
                c.execute(text("SET ROLE lector_bi"))
                with self.assertRaises(DBAPIError) as error:
                    c.execute(text(sql))
                self.assertIn("permission denied", str(error.exception.orig))
