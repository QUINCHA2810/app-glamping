"""
tools/copiar_a_v2.py sin ninguna base de datos: transformaciones con datos ficticios,
agrupamiento por bloques de 10 s, cada caso del informe, la lectura del respaldo y que
la salida no muestre secretos. También el SQL de la revisión base de alembic_v2 en modo
offline (alembic --sql), que no se conecta a nada.

La prueba contra un Postgres real está en tests/test_esquema_v2_postgres.py.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from schemas import LIMITES_TELEMETRIA
from servicios import categorias
from servicios.control_energetico import PREFIJO_ALERTA_R13

BACKEND = Path(__file__).resolve().parent.parent


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


copiar = _cargar("copiar_a_v2", BACKEND / "tools" / "copiar_a_v2.py")
migracion_v2 = _cargar("esquema_base_v2", BACKEND / "alembic_v2" / "versions" / "2b8e4f1a6c90_esquema_base_v2.py")
migracion_v2_columnas = _cargar(
    "mediciones_voltaje_fv_y_temperatura", BACKEND / "alembic_v2" / "versions" / "7c3d9a1e4b52_mediciones_voltaje_fv_y_temperatura.py"
)

FECHA_RESPALDO = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
HASH = "$2b$12$hash-ficticio-que-no-debe-salir-en-pantalla"
TOKEN = "token-ficticio-que-no-debe-salir"


def t(segundos: float) -> str:
    """Instante ficticio: 2026-11-13 12:00:00 UTC + segundos, en ISO como el respaldo."""
    return datetime.fromtimestamp(1794571200 + segundos, tz=timezone.utc).isoformat()


def respaldo_base() -> dict:
    """Un dueño (1) con un domo AGM (1), un administrador (2) asignado, una toma con el
    preset, un bombillo, acciones de R-13 y del dueño, una alerta de R-13 y lecturas."""
    return {
        "usuarios": [
            {"id": 1, "nombre": "Dueña", "apellido": "X", "correo": "duena@ejemplo.co", "contrasena_hash": HASH,
             "telefono": None, "fotografia": None, "rol": "dueño", "estado_cuenta": "activo",
             "fecha_ultimo_acceso": None, "estado_sesion": None, "token_autenticacion": TOKEN, "version_sesion": 3,
             "debe_cambiar_contrasena": False, "fecha_creacion": t(-86400), "creado_por_id": None},
            {"id": 2, "nombre": "Admin", "apellido": None, "correo": "admin@ejemplo.co", "contrasena_hash": HASH,
             "telefono": "300", "fotografia": None, "rol": "administrador", "estado_cuenta": "inactivo",
             "fecha_ultimo_acceso": None, "estado_sesion": None, "token_autenticacion": None, "version_sesion": 0,
             "debe_cambiar_contrasena": True, "fecha_creacion": t(-3600), "creado_por_id": 1},
        ],
        "domos": [
            {"id": 1, "propietario_id": 1, "nombre": "Domo", "ubicacion": "Finca", "descripcion": None,
             "estado_domo": "activo", "estado_conexion": None, "fecha_registro": t(-86400), "es_demostracion": False},
        ],
        "administrador_domo": [
            {"id": 5, "administrador_id": 2, "domo_id": 1, "permisos_asignados": None, "estado_asignacion": "activo",
             "fecha_asignacion": t(-3000)},
        ],
        "configuracion_sistema": [
            {"id": 1, "domo_id": 1, "parametros_control": None, "limites_alerta": None, "prioridades_carga": None,
             "configuracion_dispositivos": None, "fecha_modificacion": t(0),
             "parametros_energeticos": json.dumps({"bateria": {"tipo": "agm", "capacidad_wh": 1200},
                                                   "perfil_control": "conservador"})},
        ],
        "dispositivos": [
            {"id": 10, "domo_id": 1, "tipo": "toma", "nombre": "Toma cargadores", "ubicacion": "Sala", "activo": True,
             "desactivado_en": None, "categoria": "confort", "estado": "apagado", "brillo": None, "color": None,
             "temperatura_color": None, "prioridad": 20, "umbral_apagado_soc": 65.0, "umbral_reactivacion_soc": 75.0,
             "potencia": None, "energia_consumida": None, "estado_conexion": None, "ultima_comunicacion": None,
             "id_fisico": "toma1"},
            {"id": 11, "domo_id": 1, "tipo": "bombillo", "nombre": "Bombillo", "ubicacion": None, "activo": True,
             "desactivado_en": None, "categoria": "esencial", "estado": "encendido", "brillo": 40, "color": "blanco",
             "temperatura_color": "cálida", "prioridad": 40, "umbral_apagado_soc": None,
             "umbral_reactivacion_soc": None, "potencia": None, "energia_consumida": None,
             "estado_conexion": "conectado", "ultima_comunicacion": t(100), "id_fisico": "bombillo1"},
        ],
        "estado_comunicacion": [
            {"id": 1, "dispositivo_id": 10, "estado_conexion": "conectado", "senal": None,
             "identificador_dispositivo": None, "estado_mqtt": None, "ultima_comunicacion": t(50)},
            {"id": 2, "dispositivo_id": 10, "estado_conexion": "desconectado", "senal": None,
             "identificador_dispositivo": None, "estado_mqtt": None, "ultima_comunicacion": t(60)},
        ],
        "historial_acciones": [
            {"id": 100, "usuario_id": None, "domo_id": 1, "dispositivo_id": 10, "rol": "sistema",
             "accion_realizada": "apagar", "valor_anterior": "encendido", "valor_nuevo": "apagado",
             "condicion_control": "modo=supervivencia; soc=60.0", "resultado": "ok", "fecha_hora": t(10)},
            {"id": 101, "usuario_id": 1, "domo_id": 1, "dispositivo_id": 11, "rol": "dueño",
             "accion_realizada": "encender", "valor_anterior": "apagado", "valor_nuevo": "encendido",
             "condicion_control": "control manual", "resultado": "ok", "fecha_hora": t(20)},
        ],
        "alertas": [
            {"id": 7, "domo_id": 1, "tipo_alerta": "bateria_baja", "descripcion": PREFIJO_ALERTA_R13 + "Toma cargadores",
             "nivel_prioridad": "advertencia", "variable_asociada": "estado_carga_soc", "valor_detectado": 60.0,
             "valor_limite": 65.0, "estado_alerta": "atendida", "fecha_hora": t(10), "fecha_atendida": t(500)},
            {"id": 8, "domo_id": 1, "tipo_alerta": "sobrecarga", "descripcion": "Manual",
             "nivel_prioridad": "critico", "variable_asociada": None, "valor_detectado": None,
             "valor_limite": None, "estado_alerta": "activa", "fecha_hora": t(30), "fecha_atendida": None},
        ],
        "lecturas_bateria": [
            {"id": 1, "domo_id": 1, "sensor_id": None, "voltaje": 12.6, "corriente": 1.5, "potencia": 18.9,
             "estado_carga_soc": 80.0, "temperatura": None, "estado_bateria": "cargando", "fecha_hora": t(1)},
        ],
        "lecturas_fotovoltaicas": [
            {"id": 1, "domo_id": 1, "sensor_id": None, "voltaje": None, "corriente": 2.0, "potencia_generada": 120.0,
             "energia_generada": 0.33, "estado_generacion": "generando", "fecha_hora": t(2)},
        ],
        "lecturas_consumo": [
            {"id": 1, "domo_id": 1, "dispositivo_id": None, "sensor_id": None, "voltaje": 120.0, "corriente": 0.5,
             "potencia_consumida": 60.0, "energia_consumida": 0.17, "fecha_hora": t(3)},
        ],
        "lecturas_sensores": [
            {"id": 1, "domo_id": 1, "sensor_id": None, "tipo_sensor": "pzem004t", "variable_medida": "frecuencia",
             "valor": 60.0, "unidad_medida": "Hz", "estado": None, "fecha_hora": t(4)},
            {"id": 2, "domo_id": 1, "sensor_id": None, "tipo_sensor": "pzem004t",
             "variable_medida": "factor_potencia", "valor": 0.95, "unidad_medida": None, "estado": None,
             "fecha_hora": t(4)},
        ],
        "sensores": [],
    }


def transformar(datos: dict):
    return copiar.transformar(datos, FECHA_RESPALDO)


def ids_de(resultado, codigo: str, tabla: str = None) -> list:
    return sorted(
        i for (c, tb), caso in resultado.casos.items() if c == codigo and (tabla is None or tb == tabla) for i in caso.ids
    )


class Base(TestCase):
    def test_respaldo_limpio_no_tiene_casos_de_decision(self):
        r = transformar(respaldo_base())
        self.assertEqual(r.sin_aceptar(set()), [])
        self.assertEqual({tabla: len(filas) for tabla, filas in r.filas.items()}, {
            "usuarios": 2, "domos": 1, "asignaciones": 2, "dispositivos": 2, "acciones": 2, "alertas": 2,
            "mediciones": 1,
        })

    def test_usuarios_y_domos(self):
        r = transformar(respaldo_base())
        dueno, admin = r.filas["usuarios"]
        self.assertEqual(dueno["contrasena_hash"], HASH)
        self.assertTrue(dueno["activo"])
        self.assertEqual(dueno["version_sesion"], 3)
        self.assertFalse(admin["activo"])
        self.assertTrue(admin["debe_cambiar_contrasena"])
        self.assertNotIn("rol", dueno)
        self.assertNotIn("token_autenticacion", dueno)
        domo = r.filas["domos"][0]
        self.assertEqual((domo["bateria_tipo"], domo["bateria_capacidad_wh"], domo["perfil"]), ("agm", 1200.0, "conservador"))

    def test_asignaciones_conservan_id_y_las_de_dueno_van_despues(self):
        r = transformar(respaldo_base())
        self.assertEqual(r.filas["asignaciones"], [
            {"id": 5, "usuario_id": 2, "domo_id": 1, "rol": "administrador"},
            {"id": 6, "usuario_id": 1, "domo_id": 1, "rol": "dueno"},
        ])

    def test_dispositivos_comunicacion_y_acciones(self):
        r = transformar(respaldo_base())
        toma, bombillo = r.filas["dispositivos"]
        # La fila más reciente de estado_comunicacion manda.
        self.assertFalse(toma["conectado"])
        self.assertEqual(toma["ultima_comunicacion"], datetime.fromisoformat(t(60)))
        self.assertTrue(bombillo["conectado"])
        self.assertEqual(bombillo["brillo"], 40)
        self.assertEqual(ids_de(r, "COMUNICACION_HISTORIAL"), [1, 2])
        sistema, manual = r.filas["acciones"]
        self.assertEqual((sistema["actor_tipo"], sistema["actor_ref"]), ("sistema", "R-13"))
        self.assertEqual((manual["actor_tipo"], manual["actor_ref"]), ("dueno", "duena@ejemplo.co"))
        self.assertNotIn("domo_id", manual)

    def test_alertas(self):
        r = transformar(respaldo_base())
        de_r13, manual = r.filas["alertas"]
        self.assertTrue(de_r13["creada_por_r13"])
        self.assertFalse(manual["creada_por_r13"])
        self.assertEqual(de_r13["mensaje"], PREFIJO_ALERTA_R13 + "Toma cargadores")
        self.assertEqual((de_r13["variable"], de_r13["limite"]), ("estado_carga_soc", 65.0))

    def test_columnas_descartadas_con_valor(self):
        r = transformar(respaldo_base())
        self.assertEqual(r.descartadas["usuarios.token_autenticacion"], 1)
        self.assertEqual(r.descartadas["usuarios.creado_por_id"], 1)
        self.assertNotIn("domos.estado_domo", r.descartadas)  # 'activo' es el valor de siempre


class Bloques(TestCase):
    def test_inicio_de_bloque(self):
        self.assertEqual(copiar.inicio_de_bloque(datetime.fromisoformat(t(19.999))), datetime.fromisoformat(t(10)))
        self.assertEqual(copiar.inicio_de_bloque(datetime.fromisoformat(t(20))), datetime.fromisoformat(t(20)))
        # Otra zona horaria, mismo instante.
        bogota = datetime.fromisoformat("2026-11-13T07:00:05-05:00")
        self.assertEqual(copiar.inicio_de_bloque(bogota), datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))

    def test_las_cuatro_tablas_se_juntan_en_una_fila(self):
        r = transformar(respaldo_base())
        (fila,) = r.filas["mediciones"]
        self.assertEqual(fila["fecha_hora"], datetime.fromisoformat(t(0)))
        self.assertEqual(fila["domo_id"], 1)
        self.assertEqual((fila["soc_pct"], fila["estado_bateria"], fila["potencia_bateria_w"]), (80.0, "cargando", 18.9))
        self.assertEqual((fila["corriente_fv_a"], fila["potencia_fv_w"], fila["energia_fv_wh"]), (2.0, 120.0, 0.33))
        self.assertEqual((fila["voltaje_ac_v"], fila["potencia_consumo_w"], fila["energia_consumo_wh"]), (120.0, 60.0, 0.17))
        self.assertEqual((fila["frecuencia_hz"], fila["factor_potencia"]), (60.0, 0.95))
        self.assertEqual(set(fila), set(copiar.COLUMNAS_V2["mediciones"]))

    def test_instantaneos_de_la_ultima_y_energia_sumada(self):
        datos = respaldo_base()
        base = datos["lecturas_consumo"][0]
        datos["lecturas_consumo"] = [
            {**base, "id": 3, "potencia_consumida": 70.0, "energia_consumida": 0.2, "fecha_hora": t(9.5)},
            {**base, "id": 2, "potencia_consumida": 65.0, "energia_consumida": 0.18, "fecha_hora": t(5)},
            {**base, "id": 4, "potencia_consumida": 10.0, "energia_consumida": 0.03, "fecha_hora": t(11)},
        ] + datos["lecturas_consumo"]
        # Misma fecha que la última: desempata el id.
        datos["lecturas_consumo"].append({**base, "id": 5, "potencia_consumida": 71.0, "energia_consumida": 0.21,
                                          "fecha_hora": t(9.5)})
        r = transformar(datos)
        primero, segundo = r.filas["mediciones"]
        # Instantáneos: los de la última (id 5). Energía: las cuatro lecturas del bloque.
        self.assertEqual((primero["potencia_consumo_w"], primero["voltaje_ac_v"]), (71.0, 120.0))
        self.assertAlmostEqual(primero["energia_consumo_wh"], 0.17 + 0.18 + 0.2 + 0.21)
        self.assertEqual(primero["soc_pct"], 80.0)  # otras tablas del bloque intactas
        self.assertEqual(primero["energia_fv_wh"], 0.33)  # la FV no tenía duplicadas
        self.assertEqual(segundo["fecha_hora"], datetime.fromisoformat(t(10)))
        self.assertEqual((segundo["potencia_consumo_w"], segundo["energia_consumo_wh"]), (10.0, 0.03))
        self.assertIsNone(segundo["soc_pct"])
        self.assertEqual(ids_de(r, "LECTURA_REEMPLAZADA", "lecturas_consumo"), [1, 2, 3])
        self.assertAlmostEqual(r.energia_sumada_wh["lecturas_consumo"], 0.17 + 0.18 + 0.2)
        self.assertEqual(r.sin_aceptar(set()), [])  # informativo

    def test_la_energia_suma_solo_los_valores_validos(self):
        datos = respaldo_base()
        base = datos["lecturas_fotovoltaicas"][0]
        datos["lecturas_fotovoltaicas"] += [
            {**base, "id": 2, "potencia_generada": 130.0, "energia_generada": None, "fecha_hora": t(5)},
            {**base, "id": 3, "potencia_generada": 140.0, "energia_generada": "NaN", "fecha_hora": t(6)},
            {**base, "id": 4, "potencia_generada": 150.0, "energia_generada": 0.4, "fecha_hora": t(7)},
        ]
        r = transformar(datos)
        (fila,) = r.filas["mediciones"]
        self.assertEqual(fila["potencia_fv_w"], 150.0)
        self.assertAlmostEqual(fila["energia_fv_wh"], 0.33 + 0.4)
        self.assertEqual(ids_de(r, "MEDIDA_FUERA_DE_RANGO"), [3])

    def test_energia_total_por_domo_en_el_informe(self):
        """DB-3: el ensayo dice cuánta energía hay en el respaldo y cuánta queda en v2."""
        datos = respaldo_base()
        fv, consumo = datos["lecturas_fotovoltaicas"][0], datos["lecturas_consumo"][0]
        datos["lecturas_fotovoltaicas"] += [
            {**fv, "id": 2, "energia_generada": 0.5, "fecha_hora": t(5)},  # mismo bloque: se suma
            {**fv, "id": 3, "energia_generada": 2.0, "fecha_hora": t(25)},
        ]
        # El consumo de un dispositivo no es el del domo: no cuenta en ninguno de los dos lados.
        datos["lecturas_consumo"].append({**consumo, "id": 2, "dispositivo_id": 1, "energia_consumida": 9.0, "fecha_hora": t(6)})
        r = transformar(datos)
        self.assertEqual(set(r.energia_por_domo), {1})
        v1, v2 = r.energia_por_domo[1]["energia_fv_wh"]
        self.assertAlmostEqual(v1, 0.33 + 0.5 + 2.0)
        self.assertAlmostEqual(v2, v1)
        self.assertEqual(r.energia_por_domo[1]["energia_consumo_wh"], [0.17, 0.17])
        texto = chr(10).join(copiar.informe(r, set(), False))
        self.assertIn("domo 1: fotovoltaica 0.0028 → 0.0028; consumo 0.0002 → 0.0002", texto)
        self.assertNotIn("diferencia", texto)

    def test_la_energia_que_no_pasa_se_ve_como_diferencia(self):
        datos = respaldo_base()
        fv = datos["lecturas_fotovoltaicas"][0]
        # Sin fecha: no se copia, pero estaba en el respaldo.
        datos["lecturas_fotovoltaicas"].append({**fv, "id": 2, "energia_generada": 1000.0, "fecha_hora": None})
        r = transformar(datos)
        texto = chr(10).join(copiar.informe(r, set(), False))
        self.assertIn("fotovoltaica 1.0003 → 0.0003 (diferencia -1.0000)", texto)
        self.assertEqual(ids_de(r, "LECTURA_SIN_FECHA"), [2])

    def test_sin_ninguna_energia_queda_nula(self):
        datos = respaldo_base()
        base = {**datos["lecturas_consumo"][0], "energia_consumida": None}
        datos["lecturas_consumo"] = [base, {**base, "id": 2, "fecha_hora": t(8)}]
        (fila,) = transformar(datos).filas["mediciones"]
        self.assertIsNone(fila["energia_consumo_wh"])

    def test_voltaje_fv_y_temperatura_se_copian(self):
        datos = respaldo_base()
        datos["lecturas_fotovoltaicas"][0]["voltaje"] = 48.5
        datos["lecturas_bateria"][0]["temperatura"] = 26.0
        r = transformar(datos)
        (fila,) = r.filas["mediciones"]
        self.assertEqual((fila["voltaje_fv_v"], fila["temperatura_bateria_c"]), (48.5, 26.0))
        self.assertNotIn("lecturas_fotovoltaicas.voltaje", r.descartadas)
        self.assertNotIn("lecturas_bateria.temperatura", r.descartadas)

    def test_temperatura_fuera_de_rango_queda_nula(self):
        datos = respaldo_base()
        datos["lecturas_bateria"][0]["temperatura"] = 200.0
        r = transformar(datos)
        self.assertIsNone(r.filas["mediciones"][0]["temperatura_bateria_c"])
        self.assertEqual(ids_de(r, "MEDIDA_FUERA_DE_RANGO"), [1])

    def test_la_ultima_reemplaza_la_lectura_entera_aunque_traiga_nulos(self):
        datos = respaldo_base()
        datos["lecturas_bateria"].append({**datos["lecturas_bateria"][0], "id": 2, "estado_carga_soc": None,
                                          "fecha_hora": t(6)})
        (fila,) = transformar(datos).filas["mediciones"]
        self.assertIsNone(fila["soc_pct"])
        self.assertEqual(fila["voltaje_bateria_v"], 12.6)

    def test_cada_variable_del_pzem_es_su_propia_fuente(self):
        datos = respaldo_base()
        datos["lecturas_sensores"].append({**datos["lecturas_sensores"][0], "id": 3, "valor": 59.9, "fecha_hora": t(7)})
        r = transformar(datos)
        (fila,) = r.filas["mediciones"]
        self.assertEqual((fila["frecuencia_hz"], fila["factor_potencia"]), (59.9, 0.95))
        self.assertEqual(ids_de(r, "LECTURA_REEMPLAZADA", "lecturas_sensores"), [1])

    def test_domos_distintos_no_se_mezclan(self):
        datos = respaldo_base()
        datos["domos"].append({**datos["domos"][0], "id": 2})
        datos["lecturas_bateria"].append({**datos["lecturas_bateria"][0], "id": 2, "domo_id": 2})
        r = transformar(datos)
        self.assertEqual([(m["domo_id"], m["soc_pct"]) for m in r.filas["mediciones"]], [(1, 80.0), (2, 80.0)])
        self.assertEqual(ids_de(r, "LECTURA_REEMPLAZADA"), [])


class Casos(TestCase):
    def assertCaso(self, datos, codigo, tabla, ids, decide=True):
        r = transformar(datos)
        self.assertEqual(ids_de(r, codigo, tabla), ids, codigo)
        self.assertEqual(copiar.CASOS[codigo][0], decide)
        if decide:
            self.assertIn(codigo, r.sin_aceptar(set()))
            self.assertNotIn(codigo, r.sin_aceptar({codigo}))
        return r

    def test_accion_sin_dispositivo(self):
        datos = respaldo_base()
        datos["historial_acciones"][0]["dispositivo_id"] = None
        r = self.assertCaso(datos, "ACCION_SIN_DISPOSITIVO", "historial_acciones", [100])
        self.assertEqual([a["id"] for a in r.filas["acciones"]], [101])

    def test_usuario_sin_domo(self):
        datos = respaldo_base()
        datos["administrador_domo"] = []
        r = self.assertCaso(datos, "USUARIO_SIN_DOMO", "usuarios", [2], decide=False)
        self.assertEqual(len(r.filas["usuarios"]), 2)

    def test_domo_sin_dueno(self):
        datos = respaldo_base()
        datos["domos"][0]["propietario_id"] = 99
        r = self.assertCaso(datos, "DOMO_SIN_DUENO", "domos", [1])
        self.assertEqual([a["rol"] for a in r.filas["asignaciones"]], ["administrador"])

    def test_domo_con_propietario_administrador(self):
        datos = respaldo_base()
        datos["usuarios"][0]["rol"] = "administrador"
        r = self.assertCaso(datos, "DOMO_DUENO_ROL_ADMIN", "domos", [1])
        self.assertIn({"id": 6, "usuario_id": 1, "domo_id": 1, "rol": "dueno"}, r.filas["asignaciones"])

    def test_medidas_fuera_de_rango_quedan_nulas(self):
        datos = respaldo_base()
        datos["lecturas_fotovoltaicas"][0]["potencia_generada"] = -150.0
        datos["lecturas_bateria"][0]["estado_carga_soc"] = "NaN"  # así escribe NaN respaldo_bd.py
        datos["lecturas_consumo"][0]["energia_consumida"] = "Infinity"
        r = transformar(datos)
        (fila,) = r.filas["mediciones"]
        self.assertIsNone(fila["potencia_fv_w"])
        self.assertIsNone(fila["soc_pct"])
        self.assertIsNone(fila["energia_consumo_wh"])
        self.assertEqual(fila["corriente_fv_a"], 2.0)
        self.assertEqual(ids_de(r, "MEDIDA_FUERA_DE_RANGO"), [1, 1, 1])
        self.assertIn("MEDIDA_FUERA_DE_RANGO", r.sin_aceptar(set()))

    def test_los_limites_de_v1_si_caben(self):
        datos = respaldo_base()
        datos["lecturas_fotovoltaicas"][0]["potencia_generada"] = -100.0
        datos["lecturas_bateria"][0]["estado_carga_soc"] = 100.0
        (fila,) = transformar(datos).filas["mediciones"]
        self.assertEqual((fila["potencia_fv_w"], fila["soc_pct"]), (-100.0, 100.0))

    def test_consumo_por_dispositivo_no_se_copia(self):
        datos = respaldo_base()
        datos["lecturas_consumo"][0]["dispositivo_id"] = 10
        r = self.assertCaso(datos, "CONSUMO_POR_DISPOSITIVO", "lecturas_consumo", [1])
        self.assertIsNone(r.filas["mediciones"][0]["potencia_consumo_w"])

    def test_sensor_id_se_pierde_y_se_informa(self):
        datos = respaldo_base()
        datos["lecturas_bateria"][0]["sensor_id"] = 3
        datos["sensores"] = [{"id": 3}]
        r = transformar(datos)
        self.assertEqual(r.descartadas["lecturas_bateria.sensor_id"], 1)
        self.assertEqual(r.descartadas["sensores (tabla completa)"], 1)
        self.assertEqual(r.filas["mediciones"][0]["soc_pct"], 80.0)

    def test_variable_de_sensor_sin_columna(self):
        datos = respaldo_base()
        datos["lecturas_sensores"][0]["variable_medida"] = "temperatura"
        self.assertCaso(datos, "SENSOR_SIN_COLUMNA", "lecturas_sensores", [1])

    def test_lectura_sin_fecha(self):
        datos = respaldo_base()
        datos["lecturas_bateria"][0]["fecha_hora"] = None
        self.assertCaso(datos, "LECTURA_SIN_FECHA", "lecturas_bateria", [1])

    def test_alerta_atendida_sin_fecha_pasa_a_cerrada(self):
        datos = respaldo_base()
        datos["alertas"][0]["fecha_atendida"] = None
        r = self.assertCaso(datos, "ALERTA_ATENDIDA_SIN_FECHA", "alertas", [7])
        self.assertEqual(r.filas["alertas"][0]["estado"], "cerrada")

    def test_alerta_sin_tipo(self):
        datos = respaldo_base()
        datos["alertas"][1]["tipo_alerta"] = None
        r = self.assertCaso(datos, "ALERTA_INVALIDA", "alertas", [8])
        self.assertEqual([a["id"] for a in r.filas["alertas"]], [7])

    def test_asignacion_inactiva(self):
        datos = respaldo_base()
        datos["administrador_domo"][0]["estado_asignacion"] = "inactivo"
        r = self.assertCaso(datos, "ASIGNACION_NO_ACTIVA", "administrador_domo", [5])
        self.assertEqual(ids_de(r, "USUARIO_SIN_DOMO"), [2])
        self.assertEqual(r.admins_sin_domo, [2])
        self.assertIn("Administradores que quedan sin ningún domo: 1 (id: 2)", "\n".join(copiar.informe(r, set(), False)))

    def test_un_dueno_sin_domo_no_cuenta_como_administrador(self):
        datos = respaldo_base()
        datos["usuarios"].append({**datos["usuarios"][0], "id": 3, "correo": "otro@ejemplo.co"})
        r = transformar(datos)
        self.assertEqual(ids_de(r, "USUARIO_SIN_DOMO"), [3])
        self.assertEqual(r.admins_sin_domo, [])
        self.assertIn("Administradores que quedan sin ningún domo: 0", "\n".join(copiar.informe(r, set(), False)))

    def test_asignacion_sobre_su_propio_domo(self):
        datos = respaldo_base()
        datos["administrador_domo"][0]["administrador_id"] = 1
        self.assertCaso(datos, "ASIGNACION_DEL_DUENO", "administrador_domo", [5])

    def test_asignacion_a_un_dueno(self):
        datos = respaldo_base()
        datos["usuarios"][1]["rol"] = "dueño"
        self.assertCaso(datos, "ASIGNACION_ROL_DUENO", "administrador_domo", [5])

    def test_asignacion_duplicada(self):
        datos = respaldo_base()
        datos["administrador_domo"].append({**datos["administrador_domo"][0], "id": 9})
        r = self.assertCaso(datos, "ASIGNACION_DUPLICADA", "administrador_domo", [9])
        self.assertEqual(len(r.filas["asignaciones"]), 2)

    def test_referencia_rota(self):
        datos = respaldo_base()
        datos["alertas"][0]["domo_id"] = 99
        self.assertCaso(datos, "REFERENCIA_ROTA", "alertas", [7])

    def test_dispositivo_de_tipo_otro_y_sus_acciones(self):
        datos = respaldo_base()
        datos["dispositivos"][0]["tipo"] = "otro"
        r = self.assertCaso(datos, "DISPOSITIVO_INVALIDO", "dispositivos", [10])
        self.assertEqual(ids_de(r, "ACCION_DE_DISPOSITIVO_OMITIDO"), [100])
        self.assertEqual([d["id"] for d in r.filas["dispositivos"]], [11])

    def test_bombillo_no_esencial(self):
        datos = respaldo_base()
        datos["dispositivos"][1]["categoria"] = "confort"
        r = self.assertCaso(datos, "BOMBILLO_NO_ESENCIAL", "dispositivos", [11])
        self.assertEqual(r.filas["dispositivos"][1]["categoria"], "esencial")

    def test_brillo_en_toma(self):
        datos = respaldo_base()
        datos["dispositivos"][0]["brillo"] = 50
        r = self.assertCaso(datos, "BRILLO_INVALIDO", "dispositivos", [10])
        self.assertIsNone(r.filas["dispositivos"][0]["brillo"])

    def test_desactivado_incoherente(self):
        datos = respaldo_base()
        datos["dispositivos"][0]["activo"] = False  # sin desactivado_en: la fecha de su última acción
        datos["dispositivos"][1]["desactivado_en"] = t(5)  # activo con fecha: se vacía
        r = self.assertCaso(datos, "DESACTIVADO_INCOHERENTE", "dispositivos", [10, 11])
        toma, bombillo = r.filas["dispositivos"]
        self.assertEqual(toma["desactivado_en"], datetime.fromisoformat(t(10)))
        self.assertIsNone(bombillo["desactivado_en"])

    def test_accion_de_cliente(self):
        datos = respaldo_base()
        datos["historial_acciones"][1]["rol"] = "cliente"
        self.assertCaso(datos, "ACCION_ACTOR_INVALIDO", "historial_acciones", [101])

    def test_accion_de_dueno_sin_usuario(self):
        datos = respaldo_base()
        datos["historial_acciones"][1]["usuario_id"] = None
        self.assertCaso(datos, "ACCION_SIN_USUARIO", "historial_acciones", [101])

    def test_accion_con_valores_invalidos(self):
        datos = respaldo_base()
        datos["historial_acciones"][1]["valor_nuevo"] = "40"
        self.assertCaso(datos, "ACCION_VALORES_INVALIDOS", "historial_acciones", [101])

    def test_umbrales_personalizados_y_globales(self):
        datos = respaldo_base()
        datos["dispositivos"][0]["umbral_apagado_soc"] = 70.0
        datos["dispositivos"].append({**datos["dispositivos"][0], "id": 12, "id_fisico": None,
                                      "umbral_apagado_soc": None, "umbral_reactivacion_soc": None})
        datos["configuracion_sistema"][0]["parametros_energeticos"] = json.dumps({
            "bateria": {"tipo": "agm", "capacidad_wh": 1200},
            "control_energetico": {"umbral_soc_bajo": 55, "umbral_soc_recuperacion": 65},
            "otra_clave": 1,
        })
        r = transformar(datos)
        self.assertEqual(ids_de(r, "UMBRALES_PERSONALIZADOS"), [10])
        self.assertEqual(ids_de(r, "UMBRALES_DE_GLOBALES"), [12])
        self.assertEqual(ids_de(r, "UMBRALES_GLOBALES"), [1])
        self.assertEqual(ids_de(r, "CONFIG_POR_DEFECTO"), [1])  # sin perfil: conservador
        self.assertEqual(r.descartadas["configuracion_sistema.parametros_energeticos.otra_clave"], 1)
        self.assertEqual(r.sin_aceptar(set()), [])

    def test_configuracion_duplicada_usa_la_de_id_menor(self):
        datos = respaldo_base()
        datos["configuracion_sistema"].append({**datos["configuracion_sistema"][0], "id": 0,
                                               "parametros_energeticos": "no es json"})
        r = self.assertCaso(datos, "CONFIG_DUPLICADA", "configuracion_sistema", [1])
        domo = r.filas["domos"][0]
        self.assertEqual((domo["bateria_tipo"], domo["bateria_capacidad_wh"], domo["perfil"]), ("lifepo4", None, "conservador"))
        self.assertEqual(ids_de(r, "CONFIG_SIN_CAPACIDAD"), [1])

    def test_estado_de_cuenta_desconocido(self):
        datos = respaldo_base()
        datos["usuarios"][0]["estado_cuenta"] = "suspendido"
        r = self.assertCaso(datos, "USUARIO_ESTADO", "usuarios", [1])
        self.assertFalse(r.filas["usuarios"][0]["activo"])

    def test_fecha_de_creacion_nula(self):
        datos = respaldo_base()
        datos["usuarios"][0]["fecha_creacion"] = None
        r = self.assertCaso(datos, "USUARIO_FECHA_NULA", "usuarios", [1])
        self.assertEqual(r.filas["usuarios"][0]["creado_en"], FECHA_RESPALDO)

    def test_todos_los_codigos_estan_documentados(self):
        texto = (BACKEND.parent / "docs" / "modelo_v2" / "MAPEO.md").read_text(encoding="utf-8")
        faltan = [codigo for codigo in copiar.CASOS if f"`{codigo}`" not in texto]
        self.assertEqual(faltan, [])


class ConstantesCongeladas(TestCase):
    """Las copias del script coinciden con la migración v2 y con el código de v1."""

    def test_checks_iguales_a_la_migracion(self):
        for nombre in ("TIPOS_BATERIA", "PERFILES", "TIPOS_DISPOSITIVO", "CATEGORIAS", "ESTADOS_DISPOSITIVO",
                       "ACCIONES", "TIPOS_ALERTA", "NIVELES_ALERTA", "ESTADOS_ALERTA", "ESTADOS_BATERIA",
                       "CAPACIDAD_MAXIMA_WH"):
            self.assertEqual(getattr(copiar, nombre), getattr(migracion_v2, nombre), nombre)
        # Los rangos de mediciones son los de la revisión base más los de la segunda.
        self.assertEqual(copiar.RANGOS_MEDICIONES, {**migracion_v2.RANGOS_MEDICIONES, **migracion_v2_columnas.RANGOS_NUEVOS})
        # La copia escribe en la última revisión: el esquema bi (DB-3), que va después de la
        # que agrega columnas a mediciones y no toca las tablas (tests/test_bi_v2.py).
        self.assertEqual(copiar.REVISION_V2, "4e8b2c6d9f17")
        self.assertEqual(migracion_v2_columnas.down_revision, migracion_v2.revision)
        self.assertEqual(set(copiar.COLUMNAS_V2), set(migracion_v2.TABLAS))

    def test_rangos_iguales_a_los_limites_de_telemetria(self):
        origen = {
            "soc_pct": "estado_carga_soc", "voltaje_bateria_v": "voltaje", "corriente_bateria_a": "corriente",
            "potencia_bateria_w": "potencia", "corriente_fv_a": "corriente", "potencia_fv_w": "potencia_generada",
            "energia_fv_wh": "energia_generada", "voltaje_ac_v": "voltaje", "corriente_ac_a": "corriente",
            "potencia_consumo_w": "potencia_consumida", "energia_consumo_wh": "energia_consumida",
            "frecuencia_hz": "valor", "factor_potencia": "valor",
            "voltaje_fv_v": "voltaje", "temperatura_bateria_c": "temperatura",
        }
        self.assertEqual(set(origen), set(copiar.RANGOS_MEDICIONES))
        for columna, campo in origen.items():
            self.assertEqual(copiar.RANGOS_MEDICIONES[columna], LIMITES_TELEMETRIA[campo], columna)

    def test_presets_iguales_a_categorias(self):
        self.assertEqual(copiar.RESERVA_SOC_POR_BATERIA, categorias.RESERVA_SOC_POR_BATERIA)
        self.assertEqual(copiar.TIPO_BATERIA_POR_DEFECTO, categorias.TIPO_BATERIA_POR_DEFECTO)
        self.assertEqual(copiar.PERFIL_POR_DEFECTO, categorias.PERFIL_POR_DEFECTO)
        for tipo in copiar.TIPOS_BATERIA:
            for perfil in copiar.PERFILES:
                for categoria in copiar.CATEGORIAS:
                    preset = categorias.preset_de(categoria, tipo, perfil)
                    self.assertEqual(copiar.preset_umbrales(categoria, tipo, perfil),
                                     (preset.umbral_apagado_soc, preset.umbral_reactivacion_soc))

    def test_prefijo_de_alerta_de_r13(self):
        self.assertEqual(copiar.PREFIJO_ALERTA_R13, PREFIJO_ALERTA_R13)


def escribir_respaldo(carpeta: Path, datos: dict, *, manifiesto=True, filas_mal=None, sin_columna=None, origen="v1") -> None:
    tablas = {}
    for tabla, filas in datos.items():
        (carpeta / f"{tabla}.json").write_text(json.dumps(filas), encoding="utf-8")
        columnas = {c: "text" for fila in filas for c in fila} or {c: "text" for c in copiar.COLUMNAS_V1.get(tabla, ("id",))}
        columnas.pop(sin_columna if tabla == "usuarios" else None, None)
        tablas[tabla] = {"filas": len(filas) + (1 if tabla == filas_mal else 0), "columnas": columnas}
    if manifiesto:
        manifiesto = {
            "fecha": FECHA_RESPALDO.isoformat(), "version_alembic": copiar.VERSION_V1_ESPERADA, "tablas": tablas,
            "restricciones": [],
        }
        if origen is not None:
            manifiesto["origen"] = origen
        (carpeta / "_manifiesto.json").write_text(json.dumps(manifiesto), encoding="utf-8")


class Respaldo(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.carpeta = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_un_respaldo_de_v2_no_se_copia(self):
        escribir_respaldo(self.carpeta, respaldo_base(), origen="v2")
        codigo, texto = self.correr()
        self.assertEqual(codigo, 2)
        self.assertIn("Solo se copian respaldos de la base v1", texto)

    def test_un_respaldo_sin_origen_es_de_v1(self):
        escribir_respaldo(self.carpeta, respaldo_base(), origen=None)
        self.assertEqual(self.correr()[0], 0)

    def test_sin_manifiesto_se_niega(self):
        escribir_respaldo(self.carpeta, respaldo_base(), manifiesto=False)
        with self.assertRaisesRegex(copiar.RespaldoInvalido, "_manifiesto.json"):
            copiar.cargar_respaldo(self.carpeta)

    def test_filas_distintas_del_manifiesto(self):
        escribir_respaldo(self.carpeta, respaldo_base(), filas_mal="alertas")
        with self.assertRaisesRegex(copiar.RespaldoInvalido, "alertas.json"):
            copiar.cargar_respaldo(self.carpeta)

    def test_columna_faltante(self):
        escribir_respaldo(self.carpeta, respaldo_base(), sin_columna="estado_cuenta")
        with self.assertRaisesRegex(copiar.RespaldoInvalido, "estado_cuenta"):
            copiar.cargar_respaldo(self.carpeta)

    def test_tabla_faltante(self):
        datos = respaldo_base()
        del datos["estado_comunicacion"]
        escribir_respaldo(self.carpeta, datos)
        with self.assertRaisesRegex(copiar.RespaldoInvalido, "estado_comunicacion"):
            copiar.cargar_respaldo(self.carpeta)

    def correr(self, *argumentos):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = copiar.main([str(self.carpeta), *argumentos])
        return codigo, salida.getvalue()

    def test_ensayo_no_escribe_ni_muestra_secretos(self):
        datos = respaldo_base()
        datos["historial_acciones"][0]["dispositivo_id"] = None
        escribir_respaldo(self.carpeta, datos)
        with patch.object(copiar, "aplicar") as aplicar:
            codigo, texto = self.correr("--detalle")
        aplicar.assert_not_called()
        self.assertEqual(codigo, 0)
        self.assertIn("ENSAYO: no se escribió nada", texto)
        self.assertIn("ACCION_SIN_DISPOSITIVO [SIN ACEPTAR]", texto)
        for secreto in (HASH, TOKEN, "duena@ejemplo.co", "admin@ejemplo.co", "Dueña"):
            self.assertNotIn(secreto, texto)

    def test_aplicar_se_niega_con_casos_sin_aceptar(self):
        datos = respaldo_base()
        datos["historial_acciones"][0]["dispositivo_id"] = None
        escribir_respaldo(self.carpeta, datos)
        with patch.object(copiar, "aplicar") as aplicar:
            codigo, texto = self.correr("--aplicar")
        aplicar.assert_not_called()
        self.assertEqual(codigo, 1)
        self.assertIn("faltan por aceptar ACCION_SIN_DISPOSITIVO", texto)

    def test_aplicar_con_los_casos_aceptados(self):
        datos = respaldo_base()
        datos["historial_acciones"][0]["dispositivo_id"] = None
        escribir_respaldo(self.carpeta, datos)
        with patch.object(copiar, "aplicar") as aplicar:
            codigo, _ = self.correr("--aplicar", "--aceptar", "ACCION_SIN_DISPOSITIVO")
        self.assertEqual(codigo, 0)
        (resultado,), _ = aplicar.call_args
        self.assertEqual(len(resultado.filas["acciones"]), 1)

    def test_codigo_desconocido(self):
        escribir_respaldo(self.carpeta, respaldo_base())
        codigo, texto = self.correr("--aceptar", "NO_EXISTE")
        self.assertEqual(codigo, 2)

    def test_error_de_la_base_sin_el_texto_de_postgres(self):
        class Diag:
            sqlstate = "23514"
            table_name = "usuarios"
            constraint_name = "usuarios_version_sesion_check"
            column_name = None

        class ErrorPg(Exception):
            diag = Diag()

        class ErrorSa(Exception):
            orig = ErrorPg(f"Failing row contains (1, x, {HASH})")

        texto = copiar._error_seguro(ErrorSa(f"... {HASH} ..."))
        self.assertNotIn(HASH, texto)
        self.assertIn("usuarios_version_sesion_check", texto)


class MigracionOffline(TestCase):
    """alembic -c alembic_v2.ini ... --sql genera el SQL sin conectarse a nada."""

    URL_FALSA = "postgresql://prueba@localhost:5432/v2_inexistente"

    def alembic(self, *argumentos, entorno=None):
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic_v2.ini", *argumentos],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL_V2": self.URL_FALSA, "PYTHONUTF8": "1", **(entorno or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_upgrade_crea_las_7_tablas_con_sus_restricciones(self):
        resultado = self.alembic("upgrade", "head", "--sql")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        sql = resultado.stdout
        for tabla in migracion_v2.TABLAS:
            self.assertIn(f"CREATE TABLE {tabla} (", sql)
            self.assertIn(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY", sql)
        for fragmento in (
            "CREATE UNIQUE INDEX asignaciones_un_dueno_por_domo ON asignaciones (domo_id) WHERE rol = 'dueno'",
            "dispositivos_brillo_solo_bombillo_check CHECK (tipo = 'bombillo' OR brillo IS NULL)",
            "dispositivos_bombillo_esencial_check CHECK (tipo <> 'bombillo' OR categoria = 'esencial')",
            "alertas_atendida_con_fecha_check CHECK (estado <> 'atendida' OR fecha_atendida IS NOT NULL)",
            "mediciones_soc_pct_rango CHECK (soc_pct BETWEEN 0.0 AND 100.0)",
            "mediciones_domo_fecha_key UNIQUE (domo_id, fecha_hora)",
            "REFERENCES dispositivos (id) ON DELETE RESTRICT",
            "to_regclass('public.historial_acciones') IS NOT NULL",
        ):
            self.assertIn(fragmento, sql)
        # Las dos primeras revisiones no tocan bi: el esquema bi y lector_bi llegan en la
        # tercera (4e8b2c6d9f17, ronda DB-3), y lector_bi nace sin contraseña.
        hasta_columnas, bi = sql.split("-- Running upgrade 7c3d9a1e4b52 -> 4e8b2c6d9f17")
        self.assertNotIn("bi.", hasta_columnas)
        self.assertNotIn("lector_bi", hasta_columnas)
        self.assertIn("CREATE ROLE lector_bi NOLOGIN", bi)
        self.assertNotIn("PASSWORD", bi.upper())

    def test_la_tercera_revision_crea_y_quita_el_esquema_bi(self):
        abajo = self.alembic("downgrade", "4e8b2c6d9f17:7c3d9a1e4b52", "--sql")
        self.assertEqual(abajo.returncode, 0, abajo.stderr)
        for fragmento in ("DROP VIEW bi.v_pzem", "DROP VIEW bi.v_domos", "DROP SCHEMA bi;", "DROP INDEX public.mediciones_pzem_minuto_idx",
                          "DROP ROLE lector_bi"):
            self.assertIn(fragmento, abajo.stdout)
        self.assertNotIn("CASCADE", abajo.stdout)

    def test_la_segunda_revision_agrega_las_dos_columnas(self):
        resultado = self.alembic("upgrade", "head", "--sql")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        for columna, (minimo, maximo) in migracion_v2_columnas.RANGOS_NUEVOS.items():
            self.assertIn(f"ALTER TABLE mediciones ADD COLUMN {columna} FLOAT", resultado.stdout.replace("DOUBLE PRECISION", "FLOAT"))
            self.assertIn(f"CHECK ({columna} BETWEEN {minimo!r} AND {maximo!r})", resultado.stdout)
        abajo = self.alembic("downgrade", f"{migracion_v2_columnas.revision}:{migracion_v2.revision}", "--sql")
        self.assertEqual(abajo.returncode, 0, abajo.stderr)
        for columna in migracion_v2_columnas.RANGOS_NUEVOS:
            self.assertIn(f"ALTER TABLE mediciones DROP COLUMN {columna}", abajo.stdout)

    def test_downgrade_elimina_las_7_tablas(self):
        resultado = self.alembic("downgrade", f"{migracion_v2.revision}:base", "--sql")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        for tabla in migracion_v2.TABLAS:
            self.assertIn(f"DROP TABLE {tabla}", resultado.stdout)

    def test_se_niega_si_v2_es_la_misma_base_que_v1(self):
        resultado = self.alembic("upgrade", "head", "--sql", entorno={"DATABASE_URL": self.URL_FALSA})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("misma base que DATABASE_URL", resultado.stderr)
        self.assertNotIn("v2_inexistente", resultado.stderr.split("RuntimeError")[-1])

    def test_el_arbol_no_tiene_ciclos(self):
        """Cada tabla tiene a lo sumo... un camino: las FK forman un árbol desde usuarios y domos."""
        sql = self.alembic("upgrade", "head", "--sql").stdout
        aristas = set()
        for tabla in migracion_v2.TABLAS:
            cuerpo = sql.split(f"CREATE TABLE {tabla} (")[1].split(");")[0]
            for destino in migracion_v2.TABLAS:
                if f"REFERENCES {destino} (" in cuerpo:
                    aristas.add((tabla, destino))
        self.assertEqual(aristas, {
            ("asignaciones", "usuarios"), ("asignaciones", "domos"), ("dispositivos", "domos"),
            ("acciones", "dispositivos"), ("alertas", "domos"), ("mediciones", "domos"),
        })
        # 7 nodos, 6 aristas y conexo: es un árbol (un solo camino entre dos tablas).
        self.assertEqual(len(aristas), len(migracion_v2.TABLAS) - 1)
