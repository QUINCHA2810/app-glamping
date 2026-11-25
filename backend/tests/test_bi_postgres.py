"""
Esquema bi (migración a9e4c1f7b3d2) contra un Postgres de verdad, vacío y desechable.

Mismas condiciones que tests/test_migracion_postgres.py: se omite sin TEST_POSTGRES_URL y
se niega a correr fuera de esta máquina (borra public y bi, y crea y borra roles).

Para parecerse a Supabase, antes de migrar se crean anon, authenticated y service_role con
privilegios por defecto que les darían todo lo nuevo; la migración debe quitárselos. Aquí
la migración corre como superusuario; en Supabase corre como postgres, dueño de las tablas
y con BYPASSRLS: en los dos casos las vistas leen las tablas con RLS sin políticas.
"""

# Modelo v2 (ronda DB-2): este módulo prueba la línea v1 y depende de sus modelos.
import unittest  # noqa: E402

raise unittest.SkipTest(
    "Vistas bi del modelo v1 (migración a9e4c1f7b3d2): en el modelo v2 se reescriben en la ronda DB-3. Para correrlas, la etiqueta v1-modelo-actual."
)


import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, skipUnless
from urllib.parse import urlparse

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from models import Domo
from routers.estado_general import _ultima_lectura_bateria, _ultima_lectura_consumo, _ultima_lectura_fotovoltaica
from servicios.categorias import RESERVA_SOC_POR_BATERIA, ajustes_del_domo
from servicios.control_energetico import _tomas_apagadas_por_sistema, modo_actual_de
from servicios.reportes import ZONA_BOGOTA, intervalos_de_corte, resumen_del_domo
from tests.base import _modulo_migracion
from tests.test_migracion_postgres import BASE_FASE_1, INDICES_SUPABASE, URL, alembic, indices_de_supabase, vaciar
from tests.test_reportes import _cargar_exportador

NUEVA = "a9e4c1f7b3d2"
ANTERIOR = "f7d3b9e5a2c8"
MIGRACION = _modulo_migracion("a9e4c1f7b3d2_esquema_bi_para_power_bi")
VISTAS = MIGRACION.VISTAS
ROLES_API = MIGRACION.ROLES_API
CONSULTA_PREVIA = (Path(__file__).resolve().parent / "consulta_previa_a9e4c1f7b3d2.sql").read_text(encoding="utf-8")

# Valores que no deben aparecer en ninguna vista.
SECRETOS = ("Nombre-Secreto", "dueno-secreto@correo.co", "admin-secreto@correo.co", "hash-secreto-123", "id-fisico-secreto-9")
PERMISO_DENEGADO = "42501"
# Escribir en una vista con WITH o agregados: "cannot update view", antes de mirar permisos.
VISTA_NO_ACTUALIZABLE = "55000"


def _local_a_utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=ZONA_BOGOTA).astimezone(timezone.utc)


def _comprobar_url():
    if urlparse(URL).hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError("TEST_POSTGRES_URL debe apuntar a esta máquina: esta prueba borra esquemas y roles.")


def _roles_api(engine):
    """Como en Supabase: roles de la API con privilegios por defecto sobre todo lo nuevo."""
    with engine.begin() as c:
        for rol in ROLES_API:
            if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": rol}).scalar():
                c.execute(text(f"CREATE ROLE {rol} NOLOGIN"))
        roles = ", ".join(ROLES_API)
        c.execute(text(f"ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO {roles}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES GRANT ALL ON FUNCTIONS TO {roles}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES GRANT ALL ON SCHEMAS TO {roles}"))


def _quitar_roles_api(engine):
    vaciar(engine)
    with engine.begin() as c:
        roles = ", ".join(ROLES_API)
        c.execute(text(f"ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM {roles}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM {roles}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES REVOKE ALL ON SCHEMAS FROM {roles}"))
        for rol in ROLES_API:
            c.execute(text(f"DROP OWNED BY {rol}"))
            c.execute(text(f"DROP ROLE {rol}"))


def _preparar_hasta_la_anterior(engine, con_indices_de_supabase=True):
    """La cadena hasta f7d3b9e5a2c8 con lo que Supabase tiene y la cadena no reproduce:
    los CHECK antiguos, las columnas numéricas como `real` y los índices (domo_id, fecha_hora)."""
    vaciar(engine)
    assert alembic("upgrade", BASE_FASE_1).returncode == 0
    with engine.begin() as c:
        for nombre, tabla, condicion in _modulo_migracion("b7d2c4e8a1f3_integridad_fase_1").CHECK_ANTIGUOS:
            c.execute(text(f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} CHECK ({condicion})"))
        for tabla, columna in c.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type = 'double precision'"
        )).all():
            c.execute(text(f"ALTER TABLE {tabla} ALTER COLUMN {columna} TYPE real"))
        if con_indices_de_supabase:
            indices_de_supabase(c)
    resultado = alembic("upgrade", ANTERIOR)
    assert resultado.returncode == 0, resultado.stderr


def _foto(engine):
    """Lo que la migración toca, para comparar antes y después del downgrade."""
    with engine.connect() as c:
        return {
            "esquemas": c.execute(text(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' ORDER BY 1"
            )).scalars().all(),
            "roles": c.execute(text("SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY 1")).scalars().all(),
            "indices": c.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1")).all(),
            "relaciones": c.execute(text(
                "SELECT n.nspname, c.relname, c.relkind, c.relrowsecurity, c.relacl::text FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('public', 'bi') ORDER BY 1, 2"
            )).all(),
            "funciones": c.execute(text(
                "SELECT n.nspname, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname IN ('public', 'bi') ORDER BY 1, 2"
            )).all(),
            "permisos_esquema": c.execute(text("SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'")).scalar(),
        }


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class VistasBi(TestCase):
    @classmethod
    def setUpClass(cls):
        _comprobar_url()
        cls.engine = create_engine(URL, poolclass=NullPool)
        _roles_api(cls.engine)
        _preparar_hasta_la_anterior(cls.engine)
        with cls.engine.connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            cls.previa_antes = c.execute(text(CONSULTA_PREVIA)).mappings().one()
        cls._datos()
        resultado = alembic("upgrade", NUEVA)
        assert resultado.returncode == 0, resultado.stderr
        with cls.engine.connect() as c:
            cls.previa_despues = c.execute(text(CONSULTA_PREVIA)).mappings().one()
        cls.Sesion = sessionmaker(bind=cls.engine, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        _quitar_roles_api(cls.engine)
        cls.engine.dispose()

    @classmethod
    def _datos(cls):
        """Datos conocidos en los domos 1 a 3 y el domo de demostración del generador."""
        ahora = "date_trunc('minute', now())"
        with cls.engine.begin() as c:
            # now() es fijo dentro de la transacción: la referencia de todas las lecturas recientes.
            cls.minuto_ref = c.execute(text(f"SELECT timezone('America/Bogota', {ahora})")).scalar()
            cls.insertado = datetime.now(timezone.utc)
            c.execute(text(
                "INSERT INTO usuarios (id, nombre, correo, contrasena_hash, rol, estado_cuenta) VALUES "
                "(1, 'Nombre-Secreto', 'dueno-secreto@correo.co', 'hash-secreto-123', 'dueño', 'activo'), "
                "(2, 'Nombre-Secreto', 'admin-secreto@correo.co', 'hash-secreto-123', 'administrador', 'activo')"
            ))
            c.execute(text("SELECT setval(pg_get_serial_sequence('usuarios', 'id'), 2)"))
            c.execute(text(
                "INSERT INTO domos (id, propietario_id, nombre, estado_domo) VALUES "
                "(1, 1, 'Domo real', 'activo'), (2, 1, 'Domo sin configuración', 'activo'), (3, 1, 'Domo JSON roto', 'activo')"
            ))
            c.execute(text("SELECT setval(pg_get_serial_sequence('domos', 'id'), 3)"))
            c.execute(text(
                "INSERT INTO configuracion_sistema (domo_id, parametros_energeticos) VALUES "
                "(1, '{\"bateria\": {\"tipo\": \"agm\", \"capacidad_wh\": 1200}, \"perfil_control\": \"equilibrado\"}'), "
                "(3, 'esto no es JSON')"
            ))
            # Domo 1: bombillo físico y tres tomas con historia de R-13.
            c.execute(text(
                "INSERT INTO dispositivos (id, domo_id, tipo, nombre, estado, categoria, prioridad, activo, desactivado_en, id_fisico) VALUES "
                "(1, 1, 'bombillo', 'Bombillo', 'encendido', 'esencial', 40, true, NULL, 'id-fisico-secreto-9'), "
                "(2, 1, 'toma', 'Toma prescindible', 'apagado', 'prescindible', 10, true, NULL, NULL), "
                "(3, 1, 'toma', 'Toma confort', 'encendido', 'confort', 20, true, NULL, NULL), "
                "(4, 1, 'toma', 'Toma inactiva', 'apagado', 'confort', 20, false, '2026-10-01 12:00+00', NULL), "
                "(5, 2, 'toma', 'Toma del domo 2', 'apagado', 'importante', 30, true, NULL, NULL)"
            ))
            c.execute(text("SELECT setval(pg_get_serial_sequence('dispositivos', 'id'), 5)"))
            c.execute(text(
                "INSERT INTO historial_acciones (domo_id, dispositivo_id, usuario_id, rol, accion_realizada, fecha_hora) VALUES "
                "(1, 2, NULL, 'sistema', 'apagar', '2026-10-01 10:00+00'), "
                "(1, 3, NULL, 'sistema', 'apagar', '2026-10-01 10:00+00'), "
                "(1, 3, 1, 'dueño', 'encender', '2026-10-01 11:30+00'), "
                "(1, 4, NULL, 'sistema', 'apagar', '2026-10-01 10:00+00'), "
                "(2, 5, NULL, 'sistema', 'apagar', '2026-10-02 10:00+00')"
            ))
            # Domo 1: lecturas recientes, un empate de fecha (gana el id mayor) y una vieja.
            c.execute(text(
                "INSERT INTO lecturas_bateria (domo_id, estado_carga_soc, fecha_hora) VALUES "
                f"(1, 70, {ahora} - interval '3 minutes'), (1, 55, {ahora} - interval '30 seconds'), (1, 54, {ahora} - interval '30 seconds')"
            ))
            c.execute(text(
                "INSERT INTO lecturas_fotovoltaicas (domo_id, potencia_generada, fecha_hora) VALUES "
                f"(1, 100, {ahora} - interval '10 minutes' + interval '5 seconds'), "
                f"(1, 200, {ahora} - interval '10 minutes' + interval '20 seconds'), "
                f"(1, 300, {ahora} - interval '2 minutes'), "
                f"(1, 999, {ahora} - interval '50 hours')"
            ))
            c.execute(text(f"INSERT INTO lecturas_consumo (domo_id, potencia_consumida, fecha_hora) VALUES (1, 120, {ahora} - interval '90 seconds')"))
            c.execute(text(
                "INSERT INTO lecturas_sensores (domo_id, tipo_sensor, variable_medida, valor, unidad_medida, fecha_hora) VALUES "
                f"(1, 'pzem004t', 'frecuencia', 60.0, 'Hz', {ahora} - interval '5 minutes' + interval '1 second'), "
                f"(1, 'pzem004t', 'frecuencia', 59.8, 'Hz', {ahora} - interval '5 minutes' + interval '40 seconds'), "
                f"(1, 'pzem004t', 'factor_potencia', 0.9, NULL, {ahora} - interval '5 minutes' + interval '1 second'), "
                f"(1, 'pzem004t', 'factor_potencia', NULL, NULL, {ahora} - interval '5 minutes' + interval '40 seconds'), "
                f"(1, 'otro', 'frecuencia', 10, 'Hz', {ahora} - interval '5 minutes')"
            ))
            c.execute(text(
                "INSERT INTO alertas (domo_id, tipo_alerta, nivel_prioridad, estado_alerta, descripcion, valor_detectado, fecha_hora, fecha_atendida) VALUES "
                "(1, 'bateria_baja', 'critico', 'atendida', 'texto libre', 45, '2026-10-01 10:00+00', '2026-10-01 11:30+00'), "
                "(1, 'bateria_baja', 'advertencia', 'activa', 'texto libre', 55, '2026-10-02 10:00+00', NULL)"
            ))
        # Domo de demostración: 30 días que terminan en un minuto exacto.
        from tests.test_demo import g

        cls.g = g
        fin = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        with sessionmaker(bind=cls.engine)() as db:
            assert g.ejecutar(db, "dueno-secreto@correo.co", g.Parametros(), preguntar=lambda _: "s", decir=lambda _: None, fin=fin)
            cls.demo_id = db.query(Domo).filter(Domo.nombre == g.NOMBRE_DEMO).one().id

    # Ayudas ------------------------------------------------------------------

    def filas(self, sql, **params):
        with self.engine.connect() as c:
            return c.execute(text(sql), params).mappings().all()

    def como_lector(self, sql, **params):
        """Como lector_bi, en una transacción que se deshace."""
        with self.engine.connect() as c:
            c.execute(text("SET ROLE lector_bi"))
            try:
                return c.execute(text(sql), params).mappings().all()
            finally:
                c.rollback()

    def assertDenegado(self, sql, *otros_codigos):
        with self.assertRaises(DBAPIError) as error:
            self.como_lector(sql)
        self.assertIn(error.exception.orig.pgcode, (PERMISO_DENEGADO, *otros_codigos), f"{sql}: {error.exception.orig}")

    # La consulta previa ------------------------------------------------------

    def test_consulta_previa(self):
        antes, despues = self.previa_antes, self.previa_despues
        self.assertEqual((antes["version_actual"], antes["version_correcta"]), (ANTERIOR, True))
        self.assertTrue(all(antes[k] for k in ("puede_crear_roles", "sin_esquema_bi", "sin_rol_lector_bi", "sin_indices_nuevos")))
        self.assertEqual(antes["tablas_public_sin_rls"], 0)
        self.assertTrue(antes["con_indices_existentes_de_lecturas"])
        for nombre, _ in INDICES_SUPABASE:
            self.assertIn(nombre, despues["indices_existentes"])
        self.assertTrue(antes["listo_para_migrar"])
        self.assertEqual(despues["version_actual"], NUEVA)
        self.assertFalse(any(despues[k] for k in ("version_correcta", "sin_esquema_bi", "sin_rol_lector_bi", "sin_indices_nuevos", "listo_para_migrar")))
        for nombre, _, _ in MIGRACION.INDICES:
            self.assertIn(nombre, despues["indices_existentes"])

    # Vistas contra datos conocidos --------------------------------------------

    def test_v_domos(self):
        domos = {f["domo_id"]: f for f in self.filas("SELECT * FROM bi.v_domos")}
        self.assertEqual(set(domos), {1, 2, 3, self.demo_id})
        uno = domos[1]
        self.assertEqual(
            (uno["nombre"], uno["es_demostracion"], uno["origen_datos"], uno["tipo_bateria"], float(uno["capacidad_wh"]), float(uno["reserva_soc"]), uno["perfil_control"]),
            ("Domo real", False, "Domo del proyecto", "agm", 1200.0, 50.0, "equilibrado"),
        )
        for sin_ajustes in (2, 3):
            fila = domos[sin_ajustes]
            self.assertEqual((fila["tipo_bateria"], fila["capacidad_wh"], float(fila["reserva_soc"]), fila["perfil_control"]), ("lifepo4", None, 20.0, "conservador"))
        demo = domos[self.demo_id]
        self.assertEqual((demo["es_demostracion"], demo["origen_datos"]), (True, "Demostración · datos sintéticos"))
        # Igual que Python para cada domo.
        with self.Sesion() as db:
            for domo_id, fila in domos.items():
                tipo, perfil = ajustes_del_domo(db, domo_id)
                self.assertEqual((fila["tipo_bateria"], fila["perfil_control"], float(fila["reserva_soc"])), (tipo, perfil, RESERVA_SOC_POR_BATERIA[tipo]))

    def test_v_estado_actual_datos_conocidos(self):
        estado = {f["domo_id"]: f for f in self.filas("SELECT * FROM bi.v_estado_actual")}
        uno = estado[1]
        self.assertEqual((uno["soc"], uno["generacion_w"], uno["consumo_w"]), (54, 300, 120))
        self.assertEqual(uno["ultima_lectura_local"], uno["fecha_soc_local"])
        transcurridos = (datetime.now(timezone.utc) - self.insertado).total_seconds() / 60
        self.assertTrue(0 <= float(uno["minutos_desde_ultima_lectura"]) <= transcurridos + 1.5, uno["minutos_desde_ultima_lectura"])
        # Toma prescindible cortada; la confort se encendió después; la inactiva no cuenta.
        self.assertEqual((uno["estado_energetico"], uno["tomas_cortadas"]), ("ahorro", 1))
        self.assertEqual((estado[2]["estado_energetico"], estado[2]["tomas_cortadas"]), ("supervivencia", 1))
        tres = estado[3]
        self.assertEqual((tres["soc"], tres["ultima_lectura_local"], tres["minutos_desde_ultima_lectura"], tres["estado_energetico"], tres["tomas_cortadas"]), (None, None, None, "normal", 0))
        self.assertTrue(estado[self.demo_id]["es_demostracion"])
        self.assertFalse(uno["es_demostracion"])

    def test_v_estado_actual_igual_que_la_app(self):
        estado = {f["domo_id"]: f for f in self.filas("SELECT * FROM bi.v_estado_actual")}
        with self.Sesion() as db:
            for domo in db.query(Domo).all():
                with self.subTest(domo=domo.id):
                    fila = estado[domo.id]
                    bateria, fv, consumo = (f(db, domo.id) for f in (_ultima_lectura_bateria, _ultima_lectura_fotovoltaica, _ultima_lectura_consumo))
                    self.assertEqual(fila["soc"], bateria.estado_carga_soc if bateria else None)
                    self.assertEqual(fila["generacion_w"], fv.potencia_generada if fv else None)
                    self.assertEqual(fila["consumo_w"], consumo.potencia_consumida if consumo else None)
                    if bateria:
                        self.assertEqual(_local_a_utc(fila["fecha_soc_local"]), bateria.fecha_hora)
                    cortadas = [t for t, _ in _tomas_apagadas_por_sistema(db, domo)]
                    self.assertEqual((fila["estado_energetico"], fila["tomas_cortadas"]), (modo_actual_de(cortadas), len(cortadas)))

    def test_v_lecturas_minuto(self):
        filas = self.filas("SELECT * FROM bi.v_lecturas_minuto WHERE domo_id = 1 ORDER BY minuto_local")
        ahora_local = self.minuto_ref
        por_minuto = {f["minuto_local"]: f for f in filas}
        diez = por_minuto[ahora_local - timedelta(minutes=10)]
        self.assertEqual((float(diez["generacion_w"]), diez["consumo_w"], diez["soc"]), (150.0, None, None))
        self.assertEqual((diez["fecha"], diez["hora"]), (diez["minuto_local"].date(), diez["minuto_local"].hour))
        self.assertEqual(float(por_minuto[ahora_local - timedelta(minutes=1)]["soc"]), 54.5)
        self.assertEqual(float(por_minuto[ahora_local - timedelta(minutes=3)]["soc"]), 70.0)
        # La de hace 50 horas queda fuera de la ventana de 48 h.
        self.assertTrue(all(f["minuto_local"] >= ahora_local - timedelta(hours=48, minutes=1) for f in filas))
        self.assertNotIn(999.0, [f["generacion_w"] and float(f["generacion_w"]) for f in filas])
        demo = self.filas("SELECT count(*) AS n FROM bi.v_lecturas_minuto WHERE domo_id = :d", d=self.demo_id)[0]["n"]
        self.assertGreaterEqual(demo, 48 * 12 - 2)

    def test_v_lecturas_5min_igual_que_la_exportacion(self):
        exportador = _cargar_exportador()
        filas = self.filas("SELECT * FROM bi.v_lecturas_5min WHERE domo_id = :d ORDER BY inicio_local", d=self.demo_id)
        with self.Sesion() as db:
            esperado = exportador._lecturas_de_domo(db, self.demo_id, None, None, 50.0)
        self.assertEqual(len(filas), len(esperado))
        self.assertEqual(len(filas), 30 * 288)
        for fila in filas:
            cubeta = int(_local_a_utc(fila["inicio_local"]).timestamp()) // 300
            dato = esperado[cubeta]
            for columna, clave in (("generacion_w", "generacion_prom"), ("consumo_w", "consumo_prom"), ("soc", "soc_prom")):
                self.assertAlmostEqual(float(fila[columna]), dato[clave], delta=1e-3, msg=(columna, fila["inicio_local"]))
            self.assertAlmostEqual(float(fila["generacion_wh"]), dato["generacion_integral"], delta=1e-3)
            self.assertAlmostEqual(float(fila["consumo_wh"]), dato["consumo_integral"], delta=1e-3)
            self.assertAlmostEqual(float(fila["minutos_bajo_reserva"]), dato.get("segundos_bajo_reserva", 0) / 60.0, delta=1e-3)

    def test_v_lecturas_5min_suma_lo_mismo_que_reportes_por_dia(self):
        dias = self.filas(
            "SELECT fecha, sum(generacion_wh) AS g, sum(consumo_wh) AS c, sum(minutos_bajo_reserva) AS m "
            "FROM bi.v_lecturas_5min WHERE domo_id = :d GROUP BY fecha ORDER BY fecha",
            d=self.demo_id,
        )
        with self.Sesion() as db:
            reporte = resumen_del_domo(db, self.demo_id, dias[0]["fecha"], dias[-1]["fecha"])
        por_dia = {d["fecha"]: d for d in reporte["dias"]}
        for dia in dias:
            with self.subTest(dia=dia["fecha"]):
                esperado = por_dia[dia["fecha"].isoformat()]
                self.assertAlmostEqual(float(dia["g"]) / 1000, esperado["kwh_generados"] or 0, delta=1e-4)
                self.assertAlmostEqual(float(dia["c"]) / 1000, esperado["kwh_consumidos"] or 0, delta=1e-4)
        self.assertAlmostEqual(sum(float(d["m"]) for d in dias) / 60, reporte["horas_bajo_reserva"], delta=1e-3)

    def test_v_cortes_igual_que_reportes(self):
        filas = self.filas("SELECT * FROM bi.v_cortes")
        with self.Sesion() as db:
            ahora = datetime.now(timezone.utc)
            esperado = {}
            for (domo_id,) in db.query(Domo.id).all():
                for corte in intervalos_de_corte(db, domo_id, None, None, ahora):
                    esperado[(corte["dispositivo_id"], corte["inicio"])] = corte
        self.assertEqual(len(filas), len(esperado))
        self.assertGreater(len(filas), 10)
        for fila in filas:
            corte = esperado[(fila["dispositivo_id"], _local_a_utc(fila["inicio_local"]))]
            self.assertAlmostEqual(float(fila["horas_apagada"]), corte["horas"], delta=0.01)
            self.assertEqual(fila["categoria"], corte["categoria"])
            self.assertEqual((fila["fecha"], fila["hora"]), (fila["inicio_local"].date(), fila["inicio_local"].hour))
        conocidos = {f["dispositivo_id"]: f for f in filas if f["domo_id"] in (1, 2)}
        # Confort: hasta que el dueño la encendió. Inactiva: hasta que se desactivó. Las otras siguen.
        self.assertEqual((float(conocidos[3]["horas_apagada"]), conocidos[3]["vigente"]), (1.5, False))
        self.assertEqual((float(conocidos[4]["horas_apagada"]), conocidos[4]["vigente"]), (2.0, False))
        self.assertEqual(conocidos[4]["fin_local"], datetime(2026, 10, 1, 7, 0))
        self.assertTrue(conocidos[2]["vigente"] and conocidos[5]["vigente"])

    def test_v_acciones_y_v_alertas(self):
        acciones = self.filas("SELECT * FROM bi.v_acciones")
        with self.engine.connect() as c:
            total = c.execute(text("SELECT count(*) FROM historial_acciones")).scalar()
        self.assertEqual(len(acciones), total)
        self.assertTrue(all(a["automatica"] == (a["rol"] == "sistema") for a in acciones))
        encender = next(a for a in acciones if a["dispositivo_id"] == 3 and a["accion"] == "encender")
        self.assertEqual((encender["rol"], encender["momento_local"], encender["hora"]), ("dueño", datetime(2026, 10, 1, 6, 30), 6))

        alertas = {f["valor_detectado"]: f for f in self.filas("SELECT * FROM bi.v_alertas WHERE domo_id = 1")}
        atendida = alertas[45]
        self.assertEqual((atendida["momento_local"], atendida["atendida_local"], float(atendida["minutos_hasta_atendida"])), (datetime(2026, 10, 1, 5, 0), datetime(2026, 10, 1, 6, 30), 90.0))
        self.assertIsNone(alertas[55]["minutos_hasta_atendida"])
        demo = self.filas("SELECT count(*) AS n FROM bi.v_alertas WHERE domo_id = :d", d=self.demo_id)[0]["n"]
        self.assertGreater(demo, 0)

    def test_v_dispositivos(self):
        dispositivos = {f["dispositivo_id"]: f for f in self.filas("SELECT * FROM bi.v_dispositivos WHERE domo_id IN (1, 2)")}
        self.assertEqual(dispositivos[1]["real"], True)
        self.assertTrue(all(not f["real"] for i, f in dispositivos.items() if i != 1))
        self.assertEqual((dispositivos[4]["activo"], dispositivos[4]["desactivado_en_local"]), (False, datetime(2026, 10, 1, 7, 0)))
        self.assertEqual((dispositivos[2]["categoria"], dispositivos[2]["umbral_apagado_soc"]), ("prescindible", None))

    def test_v_pzem(self):
        filas = self.filas("SELECT * FROM bi.v_pzem")
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual((fila["domo_id"], float(fila["frecuencia_hz"]), float(fila["factor_potencia"])), (1, 59.9, 0.9))
        self.assertEqual(fila["minuto_local"], self.minuto_ref - timedelta(minutes=5))

    # Seguridad ---------------------------------------------------------------

    def test_lector_bi_lee_todas_las_vistas_pese_a_rls(self):
        with self.engine.connect() as c:
            sin_rls = c.execute(text(
                "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND NOT relrowsecurity"
            )).scalars().all()
            politicas = c.execute(text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")).scalar()
        self.assertEqual((sin_rls, politicas), ([], 0))
        for vista in VISTAS:
            with self.subTest(vista=vista):
                filas = self.como_lector(f"SELECT * FROM bi.{vista}")
                self.assertEqual(len(filas), len(self.filas(f"SELECT * FROM bi.{vista}")))
                self.assertGreater(len(filas), 0)

    def test_lector_bi_no_lee_public_ni_escribe(self):
        with self.engine.connect() as c:
            tablas = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")).scalars().all()
        self.assertGreaterEqual(len(tablas), 25)
        for tabla in tablas:
            with self.subTest(tabla=tabla):
                self.assertDenegado(f"SELECT 1 FROM public.{tabla} LIMIT 1")
                self.assertDenegado(f"DELETE FROM public.{tabla}")
        self.assertDenegado("INSERT INTO public.domos (propietario_id, nombre, estado_domo) VALUES (1, 'x', 'activo')")
        for vista in VISTAS:
            with self.subTest(vista=vista):
                self.assertDenegado(f"DELETE FROM bi.{vista}", VISTA_NO_ACTUALIZABLE)
        # Estas sí serían actualizables: las frena el permiso.
        self.assertDenegado("UPDATE bi.v_dispositivos SET nombre = 'x'")
        self.assertDenegado("DELETE FROM bi.v_alertas")
        self.assertDenegado("INSERT INTO bi.v_acciones (accion_id, domo_id) VALUES (999, 1)")
        self.assertDenegado("CREATE TABLE bi.nueva (x int)")
        self.assertDenegado("CREATE TABLE public.nueva (x int)")
        self.assertDenegado("CREATE VIEW bi.otra AS SELECT 1")
        self.assertDenegado("CREATE SCHEMA otro")

    def test_permisos_exactos_de_lector_bi(self):
        with self.engine.connect() as c:
            relaciones = c.execute(text(
                "SELECT n.nspname || '.' || c.relname || ':' || a.privilege_type FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
                "WHERE a.grantee = 'lector_bi'::regrole ORDER BY 1"
            )).scalars().all()
            funciones = c.execute(text(
                "SELECT p.oid::regprocedure::text FROM pg_proc p, aclexplode(p.proacl) a WHERE a.grantee = 'lector_bi'::regrole"
            )).scalars().all()
            esquemas = c.execute(text(
                "SELECT n.nspname || ':' || a.privilege_type FROM pg_namespace n, aclexplode(n.nspacl) a WHERE a.grantee = 'lector_bi'::regrole"
            )).scalars().all()
            rol = c.execute(text(
                "SELECT rolcanlogin, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication, rolconfig "
                "FROM pg_roles WHERE rolname = 'lector_bi'"
            )).one()
            miembro_de = c.execute(text("SELECT count(*) FROM pg_auth_members WHERE member = 'lector_bi'::regrole")).scalar()
        self.assertEqual(relaciones, sorted(f"bi.{v}:SELECT" for v in VISTAS))
        self.assertEqual(funciones, ["bi.json_o_vacio(text)"])
        self.assertEqual(esquemas, ["bi:USAGE"])
        self.assertEqual(tuple(rol)[:7], (False, False, False, False, False, False, False))
        self.assertEqual(sorted(rol.rolconfig), ["default_transaction_read_only=on", "statement_timeout=30s"])
        self.assertEqual(miembro_de, 0)

    def test_nada_para_public_ni_la_api_de_supabase(self):
        with self.engine.connect() as c:
            for rol in ROLES_API:
                with self.subTest(rol=rol):
                    self.assertFalse(c.execute(text("SELECT has_schema_privilege(:r, 'bi', 'USAGE')"), {"r": rol}).scalar())
                    for vista in VISTAS:
                        self.assertFalse(c.execute(text("SELECT has_table_privilege(:r, :v, 'SELECT')"), {"r": rol, "v": f"bi.{vista}"}).scalar())
                    self.assertFalse(c.execute(text("SELECT has_function_privilege(:r, 'bi.json_o_vacio(text)', 'EXECUTE')"), {"r": rol}).scalar())
            publico = c.execute(text(
                "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a WHERE c.relnamespace = 'bi'::regnamespace AND a.grantee = 0"
            )).scalar()
            esquema = c.execute(text("SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) a WHERE n.nspname = 'bi' AND a.grantee = 0")).scalar()
            opciones = c.execute(text("SELECT relname, reloptions FROM pg_class WHERE relnamespace = 'bi'::regnamespace AND reloptions IS NOT NULL")).all()
            funcion = c.execute(text("SELECT proconfig, prosecdef FROM pg_proc WHERE proname = 'json_o_vacio'")).one()
        self.assertEqual((publico, esquema, opciones), (0, 0, []))
        self.assertEqual((funcion.proconfig, funcion.prosecdef), (['search_path=""'], False))

    def test_ninguna_vista_expone_datos_sensibles(self):
        columnas = self.filas("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'bi'")
        self.assertEqual({f["table_name"] for f in columnas}, set(VISTAS))
        for fila in columnas:
            for prohibido in ("correo", "contrasena", "hash", "id_fisico", "usuario", "token", "telefono", "descripcion"):
                self.assertNotIn(prohibido, fila["column_name"], fila)
        for vista in VISTAS:
            with self.subTest(vista=vista):
                texto = " ".join(f["fila"] for f in self.filas(f"SELECT row_to_json(v)::text AS fila FROM bi.{vista} v"))
                for secreto in SECRETOS:
                    self.assertNotIn(secreto, texto)

    def test_conexion_real_de_lector_bi_con_login(self):
        """Con LOGIN, como lo dejaría el dueño: entra, lee bi, nada más, y solo lectura."""
        clave = secrets.token_hex(24)
        with self.engine.begin() as c:
            c.execute(text(f"ALTER ROLE lector_bi LOGIN PASSWORD '{clave}'"))
        self.addCleanup(self._sin_login)
        url = urlparse(URL)
        lector = create_engine(
            f"postgresql://lector_bi:{clave}@{url.hostname}:{url.port or 5432}{url.path}", poolclass=NullPool
        )
        self.addCleanup(lector.dispose)
        with lector.connect() as c:
            self.assertEqual(c.execute(text("SHOW default_transaction_read_only")).scalar(), "on")
            self.assertEqual(c.execute(text("SHOW statement_timeout")).scalar(), "30s")
            self.assertGreater(len(c.execute(text("SELECT * FROM bi.v_estado_actual")).all()), 0)
            with self.assertRaises(DBAPIError) as error:
                c.execute(text("SELECT * FROM public.usuarios"))
            self.assertEqual(error.exception.orig.pgcode, PERMISO_DENEGADO)

    def _sin_login(self):
        with self.engine.begin() as c:
            c.execute(text("ALTER ROLE lector_bi NOLOGIN PASSWORD NULL"))


@skipUnless(URL, "TEST_POSTGRES_URL no está definida")
class MigracionBiIdaYVuelta(TestCase):
    @classmethod
    def setUpClass(cls):
        _comprobar_url()
        cls.engine = create_engine(URL, poolclass=NullPool)
        _roles_api(cls.engine)

    @classmethod
    def tearDownClass(cls):
        _quitar_roles_api(cls.engine)
        cls.engine.dispose()

    def test_downgrade_limpio_aun_con_login_y_upgrade_de_nuevo(self):
        _preparar_hasta_la_anterior(self.engine)
        antes = _foto(self.engine)
        self.assertEqual(alembic("upgrade", NUEVA).returncode, 0)
        with self.engine.begin() as c:
            c.execute(text(f"ALTER ROLE lector_bi LOGIN PASSWORD '{secrets.token_hex(24)}'"))
        resultado = alembic("downgrade", ANTERIOR)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(_foto(self.engine), antes)
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT version_num FROM alembic_version")).scalar(), ANTERIOR)
            c.execute(text("SET TRANSACTION READ ONLY"))
            self.assertTrue(c.execute(text(CONSULTA_PREVIA)).mappings().one()["listo_para_migrar"])
        resultado = alembic("upgrade", NUEVA)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_downgrade_no_borra_lo_ajeno(self):
        """Si alguien creó algo en bi, el downgrade falla entero y no deja nada a medias."""
        _preparar_hasta_la_anterior(self.engine)
        self.assertEqual(alembic("upgrade", NUEVA).returncode, 0)
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE bi.ajena (x int)"))
        despues = _foto(self.engine)
        resultado = alembic("downgrade", ANTERIOR)
        self.assertNotEqual(resultado.returncode, 0)
        self.assertEqual(_foto(self.engine), despues)

    def test_sin_los_indices_de_supabase_tambien_migra(self):
        """Otra base sin los índices creados a mano: migra, las vistas responden y vuelve atrás."""
        _preparar_hasta_la_anterior(self.engine, con_indices_de_supabase=False)
        antes = _foto(self.engine)
        with self.engine.connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            fila = c.execute(text(CONSULTA_PREVIA)).mappings().one()
        self.assertEqual((fila["con_indices_existentes_de_lecturas"], fila["listo_para_migrar"]), (False, True))
        resultado = alembic("upgrade", NUEVA)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        with self.engine.connect() as c:
            for vista in VISTAS:
                c.execute(text(f"SELECT * FROM bi.{vista}")).all()
        resultado = alembic("downgrade", ANTERIOR)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(_foto(self.engine), antes)

    def test_la_migracion_falla_entera_si_el_rol_ya_existe(self):
        _preparar_hasta_la_anterior(self.engine)
        antes = _foto(self.engine)
        with self.engine.begin() as c:
            c.execute(text("CREATE ROLE lector_bi NOLOGIN"))
        resultado = alembic("upgrade", NUEVA)
        self.assertNotEqual(resultado.returncode, 0)
        with self.engine.begin() as c:
            c.execute(text("DROP ROLE lector_bi"))
        self.assertEqual(_foto(self.engine), antes)

