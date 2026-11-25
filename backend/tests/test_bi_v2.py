"""Esquema bi del modelo v2 (alembic_v2, revisión 4e8b2c6d9f17) sin Postgres.

Las vistas mismas se prueban contra Postgres en tests/test_esquema_v2_postgres.py
(clase BiSobrePostgres), que se omite sin TEST_POSTGRES_URL.
"""

import re
from pathlib import Path
from unittest import TestCase

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from servicios import categorias
from tests.base import _modulo_migracion, modulo_migracion_v2
from tests.test_reportes import _cargar_exportador

BI_V2 = modulo_migracion_v2("4e8b2c6d9f17_esquema_bi_v2")
BI_V1 = _modulo_migracion("a9e4c1f7b3d2_esquema_bi_para_power_bi")

# Las columnas de cada vista en v1, las que usa el informe de Power BI. v2 debe dar las mismas.
COLUMNAS_V1 = {
    "v_domos": ("domo_id", "nombre", "ubicacion", "es_demostracion", "origen_datos", "tipo_bateria", "capacidad_wh",
                "reserva_soc", "perfil_control"),
    "v_estado_actual": ("domo_id", "nombre", "es_demostracion", "reserva_soc", "soc", "fecha_soc_local", "generacion_w",
                        "fecha_generacion_local", "consumo_w", "fecha_consumo_local", "ultima_lectura_local",
                        "minutos_desde_ultima_lectura", "estado_energetico", "tomas_cortadas"),
    "v_lecturas_minuto": ("domo_id", "minuto_local", "fecha", "hora", "generacion_w", "consumo_w", "soc"),
    "v_lecturas_5min": ("domo_id", "inicio_local", "fecha", "hora", "generacion_w", "consumo_w", "soc", "generacion_wh",
                        "consumo_wh", "minutos_bajo_reserva"),
    "v_dispositivos": ("dispositivo_id", "domo_id", "nombre", "tipo", "categoria", "ubicacion", "estado", "activo",
                       "desactivado_en_local", "real", "prioridad", "umbral_apagado_soc", "umbral_reactivacion_soc"),
    "v_acciones": ("accion_id", "domo_id", "dispositivo_id", "momento_local", "fecha", "hora", "rol", "accion",
                   "automatica", "resultado"),
    "v_alertas": ("alerta_id", "domo_id", "momento_local", "fecha", "hora", "tipo_alerta", "nivel_prioridad",
                  "estado_alerta", "variable_asociada", "valor_detectado", "valor_limite", "atendida_local",
                  "minutos_hasta_atendida"),
    "v_cortes": ("accion_id", "domo_id", "dispositivo_id", "categoria", "inicio_local", "fin_local", "fecha", "hora",
                 "horas_apagada", "vigente"),
    "v_pzem": ("domo_id", "minuto_local", "fecha", "hora", "frecuencia_hz", "factor_potencia"),
}

TABLAS_V1 = ("lecturas_", "historial_acciones", "configuracion_sistema", "administrador_domo", "estado_comunicacion")


class CadenaDeRevisiones(TestCase):
    def test_va_despues_de_las_columnas_de_mediciones(self):
        self.assertEqual(BI_V2.down_revision, "7c3d9a1e4b52")

    def test_la_copia_escribe_en_la_ultima_revision(self):
        from tests.test_copiar_a_v2 import copiar

        self.assertEqual(copiar.REVISION_V2, BI_V2.revision)


class ConstantesCongeladas(TestCase):
    """La migración lleva copias: si cambian en Python, hace falta otra migración."""

    def test_iguales_a_categorias(self):
        self.assertEqual(BI_V2.RESERVA_SOC_POR_BATERIA, categorias.RESERVA_SOC_POR_BATERIA)
        self.assertEqual(BI_V2.PRIORIDAD_POR_CATEGORIA, categorias.PRIORIDAD_POR_CATEGORIA)
        self.assertEqual(BI_V2.SOBRE_LA_RESERVA, categorias._SOBRE_LA_RESERVA)
        self.assertEqual(BI_V2.BANDA_DE_REACTIVACION, categorias.BANDA_DE_REACTIVACION)

    def test_el_case_de_umbrales_cubre_cada_perfil_y_categoria(self):
        sql = BI_V2.SQL_VISTAS["v_dispositivos"]
        for perfil in categorias.PERFILES:
            for categoria in categorias.CATEGORIAS:
                if categoria == categorias.ESENCIAL:
                    continue
                with self.subTest(perfil=perfil, categoria=categoria):
                    margen = categorias._SOBRE_LA_RESERVA[perfil][categoria]
                    self.assertIn(f"WHEN d.perfil = '{perfil}' AND x.categoria = '{categoria}' THEN {margen}", sql)

    def test_iguales_a_reportes_y_exportacion(self):
        from servicios.reportes import HUECO_MAXIMO_S

        exportador = _cargar_exportador()
        self.assertEqual((BI_V2.ORIGEN_SINTETICO, BI_V2.ORIGEN_REAL), (exportador.ORIGEN_SINTETICO, exportador.ORIGEN_REAL))
        self.assertEqual(BI_V2.HUECO_MAXIMO_S, HUECO_MAXIMO_S)

    def test_mismas_reglas_que_v1(self):
        self.assertEqual(BI_V2.VENTANA_EN_VIVO, BI_V1.VENTANA_EN_VIVO)
        self.assertEqual(BI_V2.ZONA, BI_V1.ZONA)
        self.assertEqual(BI_V2.ROL, BI_V1.ROL)
        self.assertEqual(BI_V2.ROLES_API, BI_V1.ROLES_API)


class MismasVistasQueV1(TestCase):
    def test_mismos_nombres_y_orden(self):
        self.assertEqual(BI_V2.VISTAS, BI_V1.VISTAS)
        self.assertEqual(set(BI_V2.SQL_VISTAS), set(BI_V2.VISTAS))
        self.assertEqual(set(COLUMNAS_V1), set(BI_V1.VISTAS))

    def test_cada_columna_de_v1_sale_en_v2(self):
        """Sin Postgres: cada columna de v1 aparece como alias (o columna) en el SQL de v2. La
        lista exacta, en orden, la comprueba BiSobrePostgres contra la base."""
        for vista, columnas in COLUMNAS_V1.items():
            for columna in columnas:
                with self.subTest(vista=vista, columna=columna):
                    patron = rf"(\bAS \"?{columna}\"?\b|\.{columna}\b|^\s*{columna},?$)"
                    self.assertRegex(BI_V2.SQL_VISTAS[vista], re.compile(patron, re.MULTILINE))
                    self.assertIn(columna, BI_V1.SQL_VISTAS[vista])

    def test_ninguna_vista_lee_tablas_de_v1(self):
        for nombre, sql in BI_V2.SQL_VISTAS.items():
            for tabla in TABLAS_V1:
                with self.subTest(vista=nombre, tabla=tabla):
                    self.assertNotIn(f"public.{tabla}", sql)


class Seguridad(TestCase):
    def test_ninguna_vista_lee_usuarios_ni_id_fisico(self):
        for nombre, sql in BI_V2.SQL_VISTAS.items():
            with self.subTest(vista=nombre):
                minusculas = sql.lower()
                for prohibido in ("usuarios", "usuario_id", "asignaciones", "actor_ref", "correo", "contrasena", "hash",
                                  "token", "telefono"):
                    self.assertNotIn(prohibido, minusculas)
                self.assertEqual(minusculas.count("id_fisico"), int(nombre == "v_dispositivos"))
                if nombre == "v_dispositivos":
                    self.assertIn('(x.id_fisico is not null) as "real"', minusculas)

    def test_cada_vista_creada_con_permiso_y_sin_security_invoker(self):
        for nombre in BI_V2.VISTAS:
            self.assertIn(f"CREATE VIEW bi.{nombre} AS", BI_V2.SQL_VISTAS[nombre])
            self.assertNotIn("security_invoker", BI_V2.SQL_VISTAS[nombre])

    def test_sin_contrasenas_y_rol_sin_login(self):
        texto = Path(BI_V2.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PASSWORD", texto.upper())
        self.assertIn("CREATE ROLE {ROL} NOLOGIN", texto)
        self.assertIn("default_transaction_read_only = on", texto)
        self.assertIn("statement_timeout = '30s'", texto)
