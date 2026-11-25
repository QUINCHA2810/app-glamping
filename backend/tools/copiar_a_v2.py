"""
Copia un respaldo de la base v1 (tools/respaldo_bd.py) a la base v2
(docs/modelo_v2/MAPEO.md, alembic_v2/).

    python tools/copiar_a_v2.py <carpeta_respaldo>                     ensayo: no escribe nada
    python tools/copiar_a_v2.py <carpeta_respaldo> --detalle           ensayo con todos los id de cada caso
    python tools/copiar_a_v2.py <carpeta_respaldo> --aplicar --aceptar CODIGO[,CODIGO...]

Por defecto es un ENSAYO: no se conecta a ninguna base. Lee los .json del respaldo y
muestra cuántas filas saldrían por tabla y TODOS los casos que no encajan en v2, con
el código del caso, cuántas filas son, algunos id de ejemplo y qué haría la copia.

Hay dos clases de casos:
- informativos: la copia sigue (por ejemplo, columnas que v2 ya no tiene);
- de decisión: cambian o descartan datos. --aplicar se niega mientras haya alguno que
  no esté en --aceptar. Aceptar un código es aceptar la acción que el ensayo describe.

Con --aplicar escribe en la base de DATABASE_URL_V2 (backend/.env o el entorno; nunca
DATABASE_URL) en UNA sola transacción: o queda todo o no queda nada. Antes comprueba
que la base esté en la última revisión de alembic_v2 (REVISION_V2), sin tablas de v1 y
con las 7 tablas vacías.

Solo copia respaldos de la base v1: se niega si el manifiesto dice `"origen": "v2"`
(tools/respaldo_bd.py --origen v2). Un respaldo sin ese dato es anterior a la opción,
y por lo tanto de v1. Conserva los id originales (salvo en mediciones, que son filas nuevas)
y ajusta las secuencias para que el siguiente id no choque.

La salida nunca imprime hashes, tokens, correos, nombres ni la URL: solo cuentas,
códigos e id. Los errores de la base se muestran sin el texto de Postgres, que puede
incluir la fila completa (con el hash de la contraseña).

Uso desde backend/, con el .venv activado.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent

VERSION_V1_ESPERADA = "a9e4c1f7b3d2"
# La última de alembic_v2 (DB-3: el esquema bi, 4e8b2c6d9f17, que no agrega columnas a
# public). La copia exige que el destino esté exactamente en ella.
REVISION_V2 = "4e8b2c6d9f17"
VARIABLE_URL = "DATABASE_URL_V2"
BLOQUE_S = 10
ACTOR_R13 = "R-13"
PREFIJO_ALERTA_R13 = "R-13 cortó cargas: "  # servicios/control_energetico.PREFIJO_ALERTA_R13
MAX_EJEMPLOS = 10
FILAS_POR_INSERT = 1000

# Copias congeladas de los CHECK de alembic_v2/versions/ (2b8e4f1a6c90 y 7c3d9a1e4b52;
# las compara tests/test_copiar_a_v2.py).
TIPOS_BATERIA = ("agm", "lifepo4")
PERFILES = ("conservador", "equilibrado", "maximo_uso")
TIPOS_DISPOSITIVO = ("toma", "bombillo")
CATEGORIAS = ("esencial", "importante", "confort", "prescindible")
ESTADOS_DISPOSITIVO = ("encendido", "apagado")
ACCIONES = ("encender", "apagar")
TIPOS_ALERTA = ("bateria_baja", "consumo_elevado", "generacion_nula", "dispositivo_desconectado", "sobrecarga")
NIVELES_ALERTA = ("informativo", "advertencia", "critico")
ESTADOS_ALERTA = ("activa", "atendida", "cerrada")
ESTADOS_BATERIA = ("cargando", "descargando", "cargada", "en_reposo")
CAPACIDAD_MAXIMA_WH = 10_000_000
RANGOS_MEDICIONES = {
    "soc_pct": (0.0, 100.0),
    "voltaje_bateria_v": (-10.0, 1000.0),
    "corriente_bateria_a": (-1000.0, 1000.0),
    "potencia_bateria_w": (-100_000.0, 100_000.0),
    "corriente_fv_a": (-1000.0, 1000.0),
    "potencia_fv_w": (-100.0, 100_000.0),
    "energia_fv_wh": (0.0, 1e9),
    "voltaje_ac_v": (-10.0, 1000.0),
    "corriente_ac_a": (-1000.0, 1000.0),
    "potencia_consumo_w": (-100.0, 100_000.0),
    "energia_consumo_wh": (0.0, 1e9),
    "frecuencia_hz": (-1e9, 1e9),
    "factor_potencia": (-1e9, 1e9),
    # Revisión 7c3d9a1e4b52 (ronda DB-2, tarea 0).
    "voltaje_fv_v": (-10.0, 1000.0),
    "temperatura_bateria_c": (-50.0, 150.0),
}

# Copia congelada de servicios/categorias.py (la compara tests/test_copiar_a_v2.py): solo
# para avisar qué tomas tenían umbrales distintos del preset, que en v2 no se guardan.
RESERVA_SOC_POR_BATERIA = {"lifepo4": 20.0, "agm": 50.0}
TIPO_BATERIA_POR_DEFECTO = "lifepo4"
PERFIL_POR_DEFECTO = "conservador"
_SOBRE_LA_RESERVA = {
    "conservador": {"importante": 0.0, "confort": 15.0, "prescindible": 30.0},
    "equilibrado": {"importante": 0.0, "confort": 10.0, "prescindible": 20.0},
    "maximo_uso": {"importante": 0.0, "confort": 5.0, "prescindible": 10.0},
}
BANDA_DE_REACTIVACION = 10.0


def preset_umbrales(categoria: str, tipo_bateria: str, perfil: str) -> tuple[Optional[float], Optional[float]]:
    if categoria == "esencial":
        return None, None
    apagado = RESERVA_SOC_POR_BATERIA[tipo_bateria] + _SOBRE_LA_RESERVA[perfil][categoria]
    return apagado, apagado + BANDA_DE_REACTIVACION


# Orden de inserción (padres primero) y columnas de cada tabla v2.
COLUMNAS_V2 = {
    "usuarios": ("id", "nombre", "apellido", "correo", "contrasena_hash", "telefono", "activo",
                 "debe_cambiar_contrasena", "version_sesion", "creado_en"),
    "domos": ("id", "nombre", "ubicacion", "es_demostracion", "bateria_tipo", "bateria_capacidad_wh", "perfil",
              "creado_en"),
    "asignaciones": ("id", "usuario_id", "domo_id", "rol"),
    "dispositivos": ("id", "domo_id", "nombre", "tipo", "categoria", "id_fisico", "ubicacion", "estado", "brillo",
                     "color", "temperatura_color", "activo", "desactivado_en", "conectado", "ultima_comunicacion"),
    "acciones": ("id", "dispositivo_id", "accion", "estado_anterior", "estado_nuevo", "actor_tipo", "actor_ref",
                 "condicion", "resultado", "fecha_hora"),
    "alertas": ("id", "domo_id", "tipo", "nivel", "estado", "variable", "valor_detectado", "limite", "mensaje",
                "id_fisico_ref", "creada_por_r13", "fecha_hora", "fecha_atendida"),
    "mediciones": ("domo_id", "fecha_hora", "estado_bateria", *RANGOS_MEDICIONES),
}
TABLAS_V2 = tuple(COLUMNAS_V2)

# Tablas v1 que la copia necesita, con las columnas que lee.
COLUMNAS_V1 = {
    "usuarios": ("id", "nombre", "apellido", "correo", "contrasena_hash", "telefono", "rol", "estado_cuenta",
                 "version_sesion", "debe_cambiar_contrasena", "fecha_creacion"),
    "domos": ("id", "propietario_id", "nombre", "ubicacion", "fecha_registro", "es_demostracion"),
    "administrador_domo": ("id", "administrador_id", "domo_id", "estado_asignacion"),
    "configuracion_sistema": ("id", "domo_id", "parametros_energeticos"),
    "dispositivos": ("id", "domo_id", "tipo", "nombre", "ubicacion", "activo", "desactivado_en", "categoria", "estado",
                     "brillo", "color", "temperatura_color", "umbral_apagado_soc", "umbral_reactivacion_soc",
                     "estado_conexion", "ultima_comunicacion", "id_fisico"),
    "estado_comunicacion": ("id", "dispositivo_id", "estado_conexion", "ultima_comunicacion"),
    "historial_acciones": ("id", "usuario_id", "domo_id", "dispositivo_id", "rol", "accion_realizada", "valor_anterior",
                           "valor_nuevo", "condicion_control", "resultado", "fecha_hora"),
    "alertas": ("id", "domo_id", "tipo_alerta", "descripcion", "nivel_prioridad", "variable_asociada",
                "valor_detectado", "valor_limite", "estado_alerta", "fecha_hora", "fecha_atendida"),
    "lecturas_bateria": ("id", "domo_id", "voltaje", "corriente", "potencia", "estado_carga_soc", "temperatura", "estado_bateria",
                         "fecha_hora"),
    "lecturas_fotovoltaicas": ("id", "domo_id", "voltaje", "corriente", "potencia_generada", "energia_generada",
                               "fecha_hora"),
    "lecturas_consumo": ("id", "domo_id", "dispositivo_id", "voltaje", "corriente", "potencia_consumida",
                         "energia_consumida", "fecha_hora"),
    "lecturas_sensores": ("id", "domo_id", "variable_medida", "valor", "fecha_hora"),
}

# Lectura v1 -> columnas de mediciones. Los sensores van por variable_medida.
MAPEO_LECTURAS = {
    "lecturas_bateria": {
        "temperatura": "temperatura_bateria_c",
        "estado_carga_soc": "soc_pct",
        "voltaje": "voltaje_bateria_v",
        "corriente": "corriente_bateria_a",
        "potencia": "potencia_bateria_w",
    },
    "lecturas_fotovoltaicas": {
        "voltaje": "voltaje_fv_v",
        "corriente": "corriente_fv_a",
        "potencia_generada": "potencia_fv_w",
        "energia_generada": "energia_fv_wh",
    },
    "lecturas_consumo": {
        "voltaje": "voltaje_ac_v",
        "corriente": "corriente_ac_a",
        "potencia_consumida": "potencia_consumo_w",
        "energia_consumida": "energia_consumo_wh",
    },
}
VARIABLES_SENSOR = {"frecuencia": "frecuencia_hz", "factor_potencia": "factor_potencia"}
# Energía del intervalo: en un bloque con varias lecturas se suma (ver _mediciones).
COLUMNAS_ENERGIA = ("energia_fv_wh", "energia_consumo_wh")


def _con_valor(valor: Any) -> bool:
    return valor is not None and valor != ""


# (tabla v1, columna, condición "tiene un dato que se pierde"). Informativo.
COLUMNAS_DESCARTADAS = (
    ("usuarios", "fotografia", _con_valor),
    ("usuarios", "fecha_ultimo_acceso", _con_valor),
    ("usuarios", "estado_sesion", _con_valor),
    ("usuarios", "token_autenticacion", _con_valor),
    ("usuarios", "creado_por_id", _con_valor),
    ("domos", "descripcion", _con_valor),
    ("domos", "estado_domo", lambda v: _con_valor(v) and v != "activo"),
    ("domos", "estado_conexion", _con_valor),
    ("administrador_domo", "permisos_asignados", _con_valor),
    ("dispositivos", "potencia", _con_valor),
    ("dispositivos", "energia_consumida", _con_valor),
    ("configuracion_sistema", "parametros_control", _con_valor),
    ("configuracion_sistema", "limites_alerta", _con_valor),
    ("configuracion_sistema", "prioridades_carga", _con_valor),
    ("configuracion_sistema", "configuracion_dispositivos", _con_valor),
    ("estado_comunicacion", "senal", _con_valor),
    ("estado_comunicacion", "identificador_dispositivo", _con_valor),
    ("estado_comunicacion", "estado_mqtt", _con_valor),
    ("lecturas_bateria", "sensor_id", _con_valor),
    ("lecturas_fotovoltaicas", "sensor_id", _con_valor),
    ("lecturas_consumo", "sensor_id", _con_valor),
    ("lecturas_sensores", "sensor_id", _con_valor),
    ("lecturas_sensores", "estado", _con_valor),
)


# Casos ------------------------------------------------------------------------
# codigo -> (decide, descripción, qué hace la copia). decide=True: --aplicar exige
# --aceptar con este código si aparece.
CASOS = {
    "USUARIO_ESTADO": (True, "usuarios con estado_cuenta distinto de 'activo' e 'inactivo'",
                       "se copian desactivados (activo = false)"),
    "USUARIO_FECHA_NULA": (True, "usuarios sin fecha_creacion (creado_en es NOT NULL en v2)",
                           "creado_en = fecha del respaldo"),
    "USUARIO_SIN_DOMO": (False, "usuarios que no quedan con ningún domo (ni dueño ni administrador activo)",
                         "se copian igual; no ven ningún domo y ningún dueño los ve en Administradores"),
    "DOMO_SIN_DUENO": (True, "domos cuyo propietario_id no existe en usuarios",
                       "se copian sin asignación de dueño: nadie los ve hasta asignarles uno a mano"),
    "DOMO_DUENO_ROL_ADMIN": (True, "domos cuyo propietario tiene rol 'administrador' en v1",
                             "se le asigna el domo como 'dueno' (en v2 el rol es por domo)"),
    "DOMO_FECHA_NULA": (True, "domos sin fecha_registro (creado_en es NOT NULL en v2)",
                        "creado_en = fecha del respaldo"),
    "CONFIG_DUPLICADA": (True, "domos con más de una fila en configuracion_sistema",
                         "se usa la de id menor (la que hoy devuelve .first() en la práctica)"),
    "CONFIG_POR_DEFECTO": (False, "domos sin configuración legible, o con tipo de batería o perfil inválidos",
                           "lifepo4 / conservador, lo mismo que ya usa el código (servicios/categorias.py)"),
    "CONFIG_SIN_CAPACIDAD": (False, "domos sin una batería válida guardada (capacidad)",
                             "bateria_capacidad_wh = NULL, como hoy (la app no la muestra)"),
    "UMBRALES_GLOBALES": (False, "domos con umbrales globales guardados (control_energetico)",
                          "se descartan: en v2 cada toma usa el preset de su categoría y perfil"),
    "UMBRALES_PERSONALIZADOS": (False, "tomas con umbrales propios distintos del preset de su categoría",
                                "se descartan: en v2 usan el preset (cambia cuándo R-13 las corta)"),
    "UMBRALES_DE_GLOBALES": (False, "tomas no esenciales sin umbrales propios (seguían los globales del domo)",
                             "en v2 usan el preset de su categoría"),
    "ASIGNACION_NO_ACTIVA": (True, "asignaciones de administrador con estado distinto de 'activo'",
                             "no se copian (en v2 quitar el acceso es borrar la fila)"),
    "ASIGNACION_DEL_DUENO": (True, "asignaciones de administrador de un usuario sobre su propio domo",
                             "no se copian: queda la asignación 'dueno'"),
    "ASIGNACION_ROL_DUENO": (True, "asignaciones de administrador a usuarios con rol 'dueño' en v1 (en v1 no "
                                   "les daban acceso)", "se copian como 'administrador': ganan acceso a ese domo"),
    "ASIGNACION_DUPLICADA": (True, "más de una asignación activa del mismo usuario al mismo domo",
                             "se copia la de id menor"),
    "REFERENCIA_ROTA": (True, "filas que apuntan a un usuario, domo o dispositivo que no está en el respaldo",
                        "no se copian"),
    "DISPOSITIVO_INVALIDO": (True, "dispositivos con tipo, categoría o estado fuera de los CHECK de v2",
                             "no se copian, ni sus acciones"),
    "BOMBILLO_NO_ESENCIAL": (True, "bombillos con categoría distinta de 'esencial'", "se copian como 'esencial'"),
    "BRILLO_INVALIDO": (True, "tomas con brillo, o brillo fuera de 0-100", "brillo = NULL"),
    "DESACTIVADO_INCOHERENTE": (True, "dispositivos activos con desactivado_en, o inactivos sin él",
                                "activos: desactivado_en = NULL; inactivos: fecha de su última acción o, sin "
                                "acciones, la del respaldo (regla de la migración e6c2a8d4f1b9)"),
    "COMUNICACION_HISTORIAL": (False, "filas de estado_comunicacion (v2 guarda solo el último estado)",
                               "solo la más reciente de cada dispositivo pasa a conectado/ultima_comunicacion"),
    "ACCION_SIN_DISPOSITIVO": (True, "acciones con dispositivo_id NULL (en v2 no tendrían domo)", "no se copian"),
    "ACCION_ACTOR_INVALIDO": (True, "acciones con rol NULL, 'cliente' u otro fuera de sistema/dueño/administrador",
                              "no se copian"),
    "ACCION_SIN_USUARIO": (True, "acciones de dueño o administrador sin usuario_id (no hay correo para actor_ref)",
                           "no se copian"),
    "ACCION_VALORES_INVALIDOS": (True, "acciones con accion, estados, resultado o fecha nulos o fuera de catálogo",
                                 "no se copian"),
    "ACCION_DE_DISPOSITIVO_OMITIDO": (True, "acciones de dispositivos que no se copian", "no se copian"),
    "ALERTA_INVALIDA": (True, "alertas con tipo, nivel, estado, descripción o fecha nulos o fuera de catálogo",
                        "no se copian"),
    "ALERTA_ATENDIDA_SIN_FECHA": (True, "alertas 'atendida' sin fecha_atendida (anteriores a d5b1f3a9c2e7)",
                                  "se copian como 'cerrada' (no se inventa la fecha)"),
    "CONSUMO_POR_DISPOSITIVO": (True, "lecturas_consumo con dispositivo_id (no son el consumo total del domo)",
                                "no se copian"),
    "LECTURA_SIN_FECHA": (True, "lecturas sin fecha_hora o con una fecha ilegible", "no se copian"),
    "SENSOR_SIN_COLUMNA": (True, "lecturas_sensores con una variable que no es frecuencia ni factor_potencia",
                           "no se copian"),
    "MEDIDA_FUERA_DE_RANGO": (True, "valores de lecturas fuera de los CHECK de mediciones (o NaN/Infinity)",
                              "ese valor queda NULL; el resto de la fila se copia"),
    "LECTURA_REEMPLAZADA": (False, "lecturas con otra posterior de la misma tabla en el mismo bloque de 10 s",
                            "valores instantáneos: los de la última (por fecha_hora e id); energía del "
                            "intervalo: la suma de todas"),
    "FECHA_SIN_ZONA": (False, "fechas sin zona horaria en el respaldo", "se toman como UTC"),
}


@dataclass
class Caso:
    codigo: str
    tabla: str
    ids: list = field(default_factory=list)

    @property
    def decide(self) -> bool:
        return CASOS[self.codigo][0]


@dataclass
class Resultado:
    filas: dict = field(default_factory=lambda: {tabla: [] for tabla in TABLAS_V2})
    casos: dict = field(default_factory=dict)  # (codigo, tabla) -> Caso
    descartadas: Counter = field(default_factory=Counter)  # "tabla.columna" -> filas con valor
    energia_sumada_wh: Counter = field(default_factory=Counter)  # tabla v1 -> Wh de lecturas reemplazadas
    # domo -> {columna de energía de v2: [Wh en el respaldo v1, Wh que quedan en v2]} (DB-3)
    energia_por_domo: dict = field(default_factory=dict)
    admins_sin_domo: list = field(default_factory=list)  # id de usuarios con rol administrador en v1
    ignoradas: list = field(default_factory=list)  # tablas del respaldo que no se copian

    def caso(self, codigo: str, tabla: str, id_fila: Any) -> None:
        if codigo not in CASOS:
            raise KeyError(codigo)
        self.casos.setdefault((codigo, tabla), Caso(codigo, tabla)).ids.append(id_fila)

    def codigos(self) -> set:
        return {codigo for codigo, _ in self.casos}

    def sin_aceptar(self, aceptados: set) -> list:
        return sorted({c.codigo for c in self.casos.values() if c.decide and c.codigo not in aceptados})


# Conversión de valores ------------------------------------------------------------

def a_fecha(valor: Any, resultado: Resultado, tabla: str, id_fila: Any) -> Optional[datetime]:
    """Fecha ISO del respaldo -> datetime con zona. None si falta o es ilegible."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        fecha = valor
    else:
        try:
            fecha = datetime.fromisoformat(str(valor))
        except ValueError:
            return None
    if fecha.tzinfo is None:
        resultado.caso("FECHA_SIN_ZONA", tabla, id_fila)
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha


def a_numero(valor: Any) -> Optional[float]:
    """Número finito, o None si no hay dato. Lanza ValueError si hay algo que no es un
    número finito (NaN e Infinity llegan como texto: ver respaldo_bd._a_json)."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError("booleano")
    numero = float(valor)
    if not math.isfinite(numero):
        raise ValueError("no finito")
    return numero


def inicio_de_bloque(fecha: datetime) -> datetime:
    """Inicio del bloque de BLOQUE_S segundos que contiene `fecha`, en UTC."""
    segundos = math.floor(fecha.timestamp() / BLOQUE_S) * BLOQUE_S
    return datetime.fromtimestamp(segundos, tz=timezone.utc)


def _parametros(texto: Any) -> Optional[dict]:
    if not texto:
        return None
    try:
        valor = json.loads(texto)
    except (TypeError, ValueError):
        return None
    return valor if isinstance(valor, dict) else None


# Transformaciones ---------------------------------------------------------------

def transformar(datos: dict, fecha_respaldo: datetime) -> Resultado:
    """datos: tabla v1 -> lista de filas (dict). No toca ninguna base."""
    r = Resultado()
    for tabla, columna, tiene_dato in COLUMNAS_DESCARTADAS:
        n = sum(1 for fila in datos.get(tabla, []) if tiene_dato(fila.get(columna)))
        if n:
            r.descartadas[f"{tabla}.{columna}"] = n
    if datos.get("sensores"):
        r.descartadas["sensores (tabla completa)"] = len(datos["sensores"])

    usuarios = {u["id"]: u for u in datos.get("usuarios", [])}
    _usuarios(r, datos.get("usuarios", []), fecha_respaldo)
    domos_v2 = _domos(r, datos, usuarios, fecha_respaldo)
    _asignaciones(r, datos, usuarios, domos_v2)
    dispositivos_v2 = _dispositivos(r, datos, domos_v2, fecha_respaldo)
    _acciones(r, datos, usuarios, dispositivos_v2)
    _alertas(r, datos, domos_v2)
    _mediciones(r, datos, domos_v2)

    con_domo = {a["usuario_id"] for a in r.filas["asignaciones"]}
    for usuario in r.filas["usuarios"]:
        if usuario["id"] not in con_domo:
            r.caso("USUARIO_SIN_DOMO", "usuarios", usuario["id"])
            if usuarios[usuario["id"]].get("rol") == "administrador":
                r.admins_sin_domo.append(usuario["id"])
    return r


def _usuarios(r: Resultado, usuarios: list, fecha_respaldo: datetime) -> None:
    for u in usuarios:
        estado = u.get("estado_cuenta")
        if estado not in ("activo", "inactivo"):
            r.caso("USUARIO_ESTADO", "usuarios", u["id"])
        creado = a_fecha(u.get("fecha_creacion"), r, "usuarios", u["id"])
        if creado is None:
            r.caso("USUARIO_FECHA_NULA", "usuarios", u["id"])
            creado = fecha_respaldo
        r.filas["usuarios"].append({
            "id": u["id"],
            "nombre": u["nombre"],
            "apellido": u.get("apellido"),
            "correo": u["correo"],
            "contrasena_hash": u["contrasena_hash"],
            "telefono": u.get("telefono"),
            "activo": estado == "activo",
            "debe_cambiar_contrasena": bool(u.get("debe_cambiar_contrasena") or False),
            "version_sesion": int(u.get("version_sesion") or 0),
            "creado_en": creado,
        })


def configuracion_del_domo(r: Resultado, domo_id: int, filas_config: list) -> dict:
    """Batería y perfil de un domo a partir de sus filas de configuracion_sistema, con
    las mismas reglas de servicios/categorias.py y routers/configuracion._bateria_de."""
    filas = sorted(filas_config, key=lambda f: f["id"])
    if len(filas) > 1:
        r.caso("CONFIG_DUPLICADA", "configuracion_sistema", domo_id)
    parametros = _parametros(filas[0].get("parametros_energeticos")) if filas else None
    parametros = parametros or {}

    bateria = parametros.get("bateria")
    tipo = bateria.get("tipo") if isinstance(bateria, dict) else None
    perfil = parametros.get("perfil_control")
    if tipo not in TIPOS_BATERIA or perfil not in PERFILES:
        r.caso("CONFIG_POR_DEFECTO", "domos", domo_id)
    tipo = tipo if tipo in TIPOS_BATERIA else TIPO_BATERIA_POR_DEFECTO
    perfil = perfil if perfil in PERFILES else PERFIL_POR_DEFECTO

    capacidad = None
    if isinstance(bateria, dict) and bateria.get("tipo") in TIPOS_BATERIA:
        try:
            valor = a_numero(bateria.get("capacidad_wh"))
        except (TypeError, ValueError):
            valor = None
        if valor is not None and 0 < valor <= CAPACIDAD_MAXIMA_WH:
            capacidad = valor
    if capacidad is None:
        r.caso("CONFIG_SIN_CAPACIDAD", "domos", domo_id)

    control = parametros.get("control_energetico")
    if isinstance(control, dict) and control:
        r.caso("UMBRALES_GLOBALES", "domos", domo_id)
    otras = set(parametros) - {"bateria", "perfil_control", "control_energetico"}
    for clave in sorted(otras):
        r.descartadas[f"configuracion_sistema.parametros_energeticos.{clave}"] += 1
    return {"bateria_tipo": tipo, "bateria_capacidad_wh": capacidad, "perfil": perfil}


def _domos(r: Resultado, datos: dict, usuarios: dict, fecha_respaldo: datetime) -> dict:
    """Devuelve domo_id -> fila v2 (para validar referencias y umbrales)."""
    config_por_domo = defaultdict(list)
    for fila in datos.get("configuracion_sistema", []):
        config_por_domo[fila["domo_id"]].append(fila)
    domos_v2 = {}
    for d in datos.get("domos", []):
        creado = a_fecha(d.get("fecha_registro"), r, "domos", d["id"])
        if creado is None:
            r.caso("DOMO_FECHA_NULA", "domos", d["id"])
            creado = fecha_respaldo
        fila = {
            "id": d["id"],
            "nombre": d["nombre"],
            "ubicacion": d.get("ubicacion"),
            "es_demostracion": bool(d.get("es_demostracion") or False),
            **configuracion_del_domo(r, d["id"], config_por_domo.get(d["id"], [])),
            "creado_en": creado,
        }
        r.filas["domos"].append(fila)
        domos_v2[d["id"]] = fila

        propietario = usuarios.get(d.get("propietario_id"))
        if propietario is None:
            r.caso("DOMO_SIN_DUENO", "domos", d["id"])
            continue
        if propietario.get("rol") != "dueño":
            r.caso("DOMO_DUENO_ROL_ADMIN", "domos", d["id"])
        r.filas["asignaciones"].append(
            {"id": None, "usuario_id": propietario["id"], "domo_id": d["id"], "rol": "dueno"}
        )
    for domo_id in config_por_domo:
        if domo_id not in domos_v2:
            for fila in config_por_domo[domo_id]:
                r.caso("REFERENCIA_ROTA", "configuracion_sistema", fila["id"])
    return domos_v2


def _asignaciones(r: Resultado, datos: dict, usuarios: dict, domos_v2: dict) -> None:
    """Las de dueño ya están (sin id); se agregan las de administrador con su id
    original y al final se numeran las de dueño a continuación del mayor id."""
    duenos = {(a["usuario_id"], a["domo_id"]) for a in r.filas["asignaciones"]}
    vistas = set()
    for a in sorted(datos.get("administrador_domo", []), key=lambda f: f["id"]):
        clave = (a.get("administrador_id"), a.get("domo_id"))
        if clave[0] not in usuarios or clave[1] not in domos_v2:
            r.caso("REFERENCIA_ROTA", "administrador_domo", a["id"])
            continue
        if a.get("estado_asignacion") != "activo":
            r.caso("ASIGNACION_NO_ACTIVA", "administrador_domo", a["id"])
            continue
        if clave in duenos:
            r.caso("ASIGNACION_DEL_DUENO", "administrador_domo", a["id"])
            continue
        if clave in vistas:
            r.caso("ASIGNACION_DUPLICADA", "administrador_domo", a["id"])
            continue
        if usuarios[clave[0]].get("rol") == "dueño":
            r.caso("ASIGNACION_ROL_DUENO", "administrador_domo", a["id"])
        vistas.add(clave)
        r.filas["asignaciones"].append(
            {"id": a["id"], "usuario_id": clave[0], "domo_id": clave[1], "rol": "administrador"}
        )
    siguiente = max((a["id"] for a in r.filas["asignaciones"] if a["id"] is not None), default=0) + 1
    for a in r.filas["asignaciones"]:
        if a["id"] is None:
            a["id"] = siguiente
            siguiente += 1
    r.filas["asignaciones"].sort(key=lambda a: a["id"])


def _ultima_comunicacion(r: Resultado, dispositivo: dict, filas_com: list) -> tuple[bool, Optional[datetime]]:
    """(conectado, ultima_comunicacion): el dato más reciente entre la columna del
    dispositivo y su historial en estado_comunicacion."""
    candidatos = []
    fecha = a_fecha(dispositivo.get("ultima_comunicacion"), r, "dispositivos", dispositivo["id"])
    if fecha is not None or dispositivo.get("estado_conexion") is not None:
        candidatos.append((fecha, 0, dispositivo.get("estado_conexion")))
    for fila in filas_com:
        r.caso("COMUNICACION_HISTORIAL", "estado_comunicacion", fila["id"])
        candidatos.append((a_fecha(fila.get("ultima_comunicacion"), r, "estado_comunicacion", fila["id"]),
                           fila["id"], fila.get("estado_conexion")))
    if not candidatos:
        return False, None
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    fecha, _, estado = max(candidatos, key=lambda c: (c[0] or minimo, c[1]))
    return estado == "conectado", fecha


def _dispositivos(r: Resultado, datos: dict, domos_v2: dict, fecha_respaldo: datetime) -> dict:
    com = defaultdict(list)
    for fila in datos.get("estado_comunicacion", []):
        com[fila["dispositivo_id"]].append(fila)
    ultima_accion = {}
    for h in datos.get("historial_acciones", []):
        fecha = a_fecha(h.get("fecha_hora"), r, "historial_acciones", h["id"])
        if h.get("dispositivo_id") is not None and fecha is not None:
            ultima_accion[h["dispositivo_id"]] = max(fecha, ultima_accion.get(h["dispositivo_id"], fecha))

    dispositivos_v2 = {}
    for d in datos.get("dispositivos", []):
        if d.get("domo_id") not in domos_v2:
            r.caso("REFERENCIA_ROTA", "dispositivos", d["id"])
            continue
        if (d.get("tipo") not in TIPOS_DISPOSITIVO or d.get("categoria") not in CATEGORIAS
                or d.get("estado") not in ESTADOS_DISPOSITIVO):
            r.caso("DISPOSITIVO_INVALIDO", "dispositivos", d["id"])
            continue
        categoria = d["categoria"]
        if d["tipo"] == "bombillo" and categoria != "esencial":
            r.caso("BOMBILLO_NO_ESENCIAL", "dispositivos", d["id"])
            categoria = "esencial"
        brillo = d.get("brillo")
        if brillo is not None and (d["tipo"] != "bombillo" or not 0 <= brillo <= 100):
            r.caso("BRILLO_INVALIDO", "dispositivos", d["id"])
            brillo = None
        activo = bool(d.get("activo", True))
        desactivado = a_fecha(d.get("desactivado_en"), r, "dispositivos", d["id"])
        if activo and desactivado is not None:
            r.caso("DESACTIVADO_INCOHERENTE", "dispositivos", d["id"])
            desactivado = None
        elif not activo and desactivado is None:
            r.caso("DESACTIVADO_INCOHERENTE", "dispositivos", d["id"])
            desactivado = ultima_accion.get(d["id"], fecha_respaldo)

        domo = domos_v2[d["domo_id"]]
        if d["tipo"] == "toma" and categoria != "esencial":
            propios = (d.get("umbral_apagado_soc"), d.get("umbral_reactivacion_soc"))
            if propios == (None, None):
                r.caso("UMBRALES_DE_GLOBALES", "dispositivos", d["id"])
            else:
                preset = preset_umbrales(categoria, domo["bateria_tipo"], domo["perfil"])
                if any(p is None or v is None or not math.isclose(v, p, abs_tol=1e-3) for v, p in zip(propios, preset)):
                    r.caso("UMBRALES_PERSONALIZADOS", "dispositivos", d["id"])

        conectado, ultima = _ultima_comunicacion(r, d, com.get(d["id"], []))
        fila = {
            "id": d["id"],
            "domo_id": d["domo_id"],
            "nombre": d["nombre"],
            "tipo": d["tipo"],
            "categoria": categoria,
            "id_fisico": d.get("id_fisico"),
            "ubicacion": d.get("ubicacion"),
            "estado": d["estado"],
            "brillo": brillo,
            "color": d.get("color"),
            "temperatura_color": d.get("temperatura_color"),
            "activo": activo,
            "desactivado_en": desactivado,
            "conectado": conectado,
            "ultima_comunicacion": ultima,
        }
        r.filas["dispositivos"].append(fila)
        dispositivos_v2[d["id"]] = fila

    ids_v1 = {d["id"] for d in datos.get("dispositivos", [])}
    for dispositivo_id, filas in com.items():
        if dispositivo_id not in ids_v1:
            for fila in filas:
                r.caso("REFERENCIA_ROTA", "estado_comunicacion", fila["id"])
    return dispositivos_v2


ACTOR_POR_ROL = {"dueño": "dueno", "administrador": "administrador", "sistema": "sistema"}


def _acciones(r: Resultado, datos: dict, usuarios: dict, dispositivos_v2: dict) -> None:
    ids_v1 = {d["id"] for d in datos.get("dispositivos", [])}
    for h in datos.get("historial_acciones", []):
        dispositivo_id = h.get("dispositivo_id")
        if dispositivo_id is None:
            r.caso("ACCION_SIN_DISPOSITIVO", "historial_acciones", h["id"])
            continue
        if dispositivo_id not in dispositivos_v2:
            codigo = "ACCION_DE_DISPOSITIVO_OMITIDO" if dispositivo_id in ids_v1 else "REFERENCIA_ROTA"
            r.caso(codigo, "historial_acciones", h["id"])
            continue
        actor = ACTOR_POR_ROL.get(h.get("rol"))
        if actor is None:
            r.caso("ACCION_ACTOR_INVALIDO", "historial_acciones", h["id"])
            continue
        if actor == "sistema":
            actor_ref = ACTOR_R13
        else:
            usuario = usuarios.get(h.get("usuario_id"))
            if usuario is None:
                r.caso("ACCION_SIN_USUARIO", "historial_acciones", h["id"])
                continue
            actor_ref = usuario["correo"]
        fecha = a_fecha(h.get("fecha_hora"), r, "historial_acciones", h["id"])
        if (h.get("accion_realizada") not in ACCIONES or h.get("valor_anterior") not in ESTADOS_DISPOSITIVO
                or h.get("valor_nuevo") not in ESTADOS_DISPOSITIVO or not h.get("resultado") or fecha is None):
            r.caso("ACCION_VALORES_INVALIDOS", "historial_acciones", h["id"])
            continue
        r.filas["acciones"].append({
            "id": h["id"],
            "dispositivo_id": dispositivo_id,
            "accion": h["accion_realizada"],
            "estado_anterior": h["valor_anterior"],
            "estado_nuevo": h["valor_nuevo"],
            "actor_tipo": actor,
            "actor_ref": actor_ref,
            "condicion": h.get("condicion_control"),
            "resultado": h["resultado"],
            "fecha_hora": fecha,
        })


def _alertas(r: Resultado, datos: dict, domos_v2: dict) -> None:
    for a in datos.get("alertas", []):
        if a.get("domo_id") not in domos_v2:
            r.caso("REFERENCIA_ROTA", "alertas", a["id"])
            continue
        fecha = a_fecha(a.get("fecha_hora"), r, "alertas", a["id"])
        try:
            valor = a_numero(a.get("valor_detectado"))
            limite = a_numero(a.get("valor_limite"))
        except (TypeError, ValueError):
            valor = limite = math.nan
        if (a.get("tipo_alerta") not in TIPOS_ALERTA or a.get("nivel_prioridad") not in NIVELES_ALERTA
                or a.get("estado_alerta") not in ESTADOS_ALERTA or not a.get("descripcion") or fecha is None
                or (valor is not None and math.isnan(valor))):
            r.caso("ALERTA_INVALIDA", "alertas", a["id"])
            continue
        estado = a["estado_alerta"]
        atendida = a_fecha(a.get("fecha_atendida"), r, "alertas", a["id"])
        if estado == "atendida" and atendida is None:
            r.caso("ALERTA_ATENDIDA_SIN_FECHA", "alertas", a["id"])
            estado = "cerrada"
        r.filas["alertas"].append({
            "id": a["id"],
            "domo_id": a["domo_id"],
            "tipo": a["tipo_alerta"],
            "nivel": a["nivel_prioridad"],
            "estado": estado,
            "variable": a.get("variable_asociada"),
            "valor_detectado": valor,
            "limite": limite,
            "mensaje": a["descripcion"],
            "id_fisico_ref": None,
            "creada_por_r13": a["descripcion"].startswith(PREFIJO_ALERTA_R13),
            "fecha_hora": fecha,
            "fecha_atendida": atendida,
        })


def _medida(r: Resultado, tabla: str, id_fila: Any, columna: str, valor: Any) -> Optional[float]:
    """Valor para una columna de mediciones, o None (con el caso) si no cabe en su CHECK."""
    try:
        numero = a_numero(valor)
    except (TypeError, ValueError):
        r.caso("MEDIDA_FUERA_DE_RANGO", tabla, id_fila)
        return None
    if numero is None:
        return None
    minimo, maximo = RANGOS_MEDICIONES[columna]
    if not minimo <= numero <= maximo:
        r.caso("MEDIDA_FUERA_DE_RANGO", tabla, id_fila)
        return None
    return numero


def _lecturas_ordenadas(r: Resultado, tabla: str, filas: list, domos_v2: dict) -> list:
    """[(bloque, fila)] de las lecturas copiables, de la más antigua a la más reciente."""
    salida = []
    for fila in filas:
        if fila.get("domo_id") not in domos_v2:
            r.caso("REFERENCIA_ROTA", tabla, fila["id"])
            continue
        fecha = a_fecha(fila.get("fecha_hora"), r, tabla, fila["id"])
        if fecha is None:
            r.caso("LECTURA_SIN_FECHA", tabla, fila["id"])
            continue
        salida.append((fecha, fila))
    salida.sort(key=lambda par: (par[0], par[1]["id"]))
    return [(inicio_de_bloque(fecha), fila) for fecha, fila in salida]


def _mediciones(r: Resultado, datos: dict, domos_v2: dict) -> None:
    """Agrupa las cuatro tablas de lecturas en una fila por (domo, bloque de 10 s). Si
    una misma fuente (tabla, o variable en lecturas_sensores) tiene varias lecturas en
    el bloque, los valores instantáneos son los de la última, entera (aunque traiga
    nulos), y la energía del intervalo (energia_fv_wh, energia_consumo_wh) es la SUMA
    de las energías válidas de todas: cada lectura cubre su propio intervalo. Las
    lecturas reemplazadas se cuentan en el informe."""
    bloques: dict = {}
    vacia = {"estado_bateria": None, **{columna: None for columna in RANGOS_MEDICIONES}}
    ultima_de: dict = {}  # (domo, bloque, fuente) -> fila v1 que quedó
    energia_propia: dict = {}  # (domo, bloque, columna) -> energía válida de la última lectura

    def fila_de(domo_id, bloque):
        return bloques.setdefault((domo_id, bloque), {"domo_id": domo_id, "fecha_hora": bloque, **vacia})

    def reemplaza(tabla, fuente, domo_id, bloque, fila) -> bool:
        """Registra `fila` como la última de su fuente; True si había una anterior."""
        clave = (domo_id, bloque, fuente)
        anterior = ultima_de.get(clave)
        if anterior is not None:
            r.caso("LECTURA_REEMPLAZADA", tabla, anterior["id"])
        ultima_de[clave] = fila
        return anterior is not None

    for tabla, columnas in MAPEO_LECTURAS.items():
        for bloque, fila in _lecturas_ordenadas(r, tabla, datos.get(tabla, []), domos_v2):
            if tabla == "lecturas_consumo" and fila.get("dispositivo_id") is not None:
                r.caso("CONSUMO_POR_DISPOSITIVO", tabla, fila["id"])
                continue
            hubo_anterior = reemplaza(tabla, tabla, fila["domo_id"], bloque, fila)
            destino = fila_de(fila["domo_id"], bloque)
            for origen, columna in columnas.items():
                valor = _medida(r, tabla, fila["id"], columna, fila.get(origen))
                if columna in COLUMNAS_ENERGIA:
                    clave_energia = (fila["domo_id"], bloque, columna)
                    propia = valor
                    if hubo_anterior:
                        # La energía propia de la lectura reemplazada, no la acumulada.
                        r.energia_sumada_wh[tabla] += energia_propia.get(clave_energia) or 0.0
                    energia_propia[clave_energia] = propia
                if columna in COLUMNAS_ENERGIA and hubo_anterior:
                    previa = destino[columna]
                    valor = None if previa is None and valor is None else (previa or 0.0) + (valor or 0.0)
                    minimo, maximo = RANGOS_MEDICIONES[columna]
                    if valor is not None and not minimo <= valor <= maximo:
                        r.caso("MEDIDA_FUERA_DE_RANGO", tabla, fila["id"])
                        valor = None
                destino[columna] = valor
            if tabla == "lecturas_bateria":
                estado = fila.get("estado_bateria")
                if estado is not None and estado not in ESTADOS_BATERIA:
                    r.caso("MEDIDA_FUERA_DE_RANGO", tabla, fila["id"])
                    estado = None
                destino["estado_bateria"] = estado

    tabla = "lecturas_sensores"
    for bloque, fila in _lecturas_ordenadas(r, tabla, datos.get(tabla, []), domos_v2):
        columna = VARIABLES_SENSOR.get(fila.get("variable_medida"))
        if columna is None:
            r.caso("SENSOR_SIN_COLUMNA", tabla, fila["id"])
            continue
        reemplaza(tabla, columna, fila["domo_id"], bloque, fila)
        fila_de(fila["domo_id"], bloque)[columna] = _medida(r, tabla, fila["id"], columna, fila.get("valor"))

    r.filas["mediciones"] = [bloques[clave] for clave in sorted(bloques)]
    _totales_de_energia(r, datos, domos_v2)


# (tabla v1, columna v1, columna v2). El consumo de v1 que cuenta es el del domo (sin dispositivo_id).
ENERGIA_V1 = (
    ("lecturas_fotovoltaicas", "energia_generada", "energia_fv_wh"),
    ("lecturas_consumo", "energia_consumida", "energia_consumo_wh"),
)


def _totales_de_energia(r: Resultado, datos: dict, domos_v2: dict) -> None:
    """Energía total por domo en el respaldo y en lo que se escribiría: si la copia no pierde
    nada, son iguales. Lo que no pasa (valores fuera de rango, lecturas sin fecha) sale aparte
    en los casos del informe."""
    totales: dict = {}
    for tabla, origen, columna in ENERGIA_V1:
        for fila in datos.get(tabla, []):
            if fila.get("domo_id") not in domos_v2 or (tabla == "lecturas_consumo" and fila.get("dispositivo_id") is not None):
                continue
            try:
                valor = a_numero(fila.get(origen))
            except (TypeError, ValueError):
                continue
            if valor is not None:
                totales.setdefault(fila["domo_id"], {c: [0.0, 0.0] for _, _, c in ENERGIA_V1})[columna][0] += valor
    for fila in r.filas["mediciones"]:
        for _, _, columna in ENERGIA_V1:
            if fila.get(columna) is not None:
                totales.setdefault(fila["domo_id"], {c: [0.0, 0.0] for _, _, c in ENERGIA_V1})[columna][1] += fila[columna]
    r.energia_por_domo = totales


# Respaldo -----------------------------------------------------------------------

class RespaldoInvalido(Exception):
    pass


def cargar_respaldo(carpeta: Path) -> tuple[dict, dict]:
    """(datos, manifiesto). Se niega si falta el manifiesto, una tabla necesaria o una
    columna que la copia lee, o si un archivo no tiene las filas que dice el manifiesto."""
    manifiesto_ruta = carpeta / "_manifiesto.json"
    if not manifiesto_ruta.is_file():
        raise RespaldoInvalido(
            f"No hay _manifiesto.json en {carpeta}: el respaldo está incompleto o no es un respaldo. No se copia nada."
        )
    with manifiesto_ruta.open(encoding="utf-8") as entrada:
        manifiesto = json.load(entrada)
    tablas = manifiesto.get("tablas", {})

    datos = {}
    for tabla, info in tablas.items():
        archivo = carpeta / f"{tabla}.json"
        if not archivo.is_file():
            raise RespaldoInvalido(f"Falta {archivo.name}, que el manifiesto sí lista.")
        with archivo.open(encoding="utf-8") as entrada:
            filas = json.load(entrada)
        if len(filas) != info.get("filas"):
            raise RespaldoInvalido(
                f"{archivo.name} tiene {len(filas)} filas y el manifiesto dice {info.get('filas')}."
            )
        datos[tabla] = filas

    for tabla, columnas in COLUMNAS_V1.items():
        if tabla not in tablas:
            raise RespaldoInvalido(f"El respaldo no tiene la tabla {tabla}.")
        faltan = [c for c in columnas if c not in tablas[tabla].get("columnas", {})]
        if faltan:
            raise RespaldoInvalido(f"La tabla {tabla} del respaldo no tiene las columnas: {', '.join(faltan)}.")
    return datos, manifiesto


# Informe ------------------------------------------------------------------------

def informe(r: Resultado, aceptados: set, detalle: bool) -> list[str]:
    lineas = ["", "Filas que se escribirían en v2:"]
    for tabla in TABLAS_V2:
        lineas.append(f"  {tabla:<14}{len(r.filas[tabla]):>10}")
    ids_admins = ", ".join(str(i) for i in sorted(r.admins_sin_domo))
    lineas += ["", f"Administradores que quedan sin ningún domo: {len(r.admins_sin_domo)}"
               + (f" (id: {ids_admins})" if ids_admins else "")]

    if r.energia_por_domo:
        lineas += ["", "Energía total por domo, en kWh (respaldo v1 → v2; deben coincidir):"]
        for domo_id in sorted(r.energia_por_domo, key=str):
            partes = []
            for _, _, columna in ENERGIA_V1:
                v1, v2 = r.energia_por_domo[domo_id][columna]
                nombre = "fotovoltaica" if columna == "energia_fv_wh" else "consumo"
                diferencia = "" if abs(v1 - v2) < 0.0005 else f" (diferencia {(v2 - v1) / 1000:+.4f})"
                partes.append(f"{nombre} {v1 / 1000:.4f} → {v2 / 1000:.4f}{diferencia}")
            lineas.append(f"  domo {domo_id}: " + "; ".join(partes))

    if r.descartadas:
        lineas += ["", "Datos de v1 que no pasan a v2 (filas con valor):"]
        for nombre, n in sorted(r.descartadas.items()):
            lineas.append(f"  {nombre:<60}{n:>8}")
    if r.ignoradas:
        lineas += ["", "Tablas del respaldo que no se copian: " + ", ".join(sorted(r.ignoradas))]

    for decide, titulo in ((True, "Casos que requieren decisión (--aceptar CODIGO)"), (False, "Casos informativos")):
        casos = sorted((c for c in r.casos.values() if c.decide == decide), key=lambda c: (c.codigo, c.tabla))
        lineas += ["", f"{titulo}: {len(casos) or 'ninguno'}"]
        for c in casos:
            _, descripcion, accion = CASOS[c.codigo]
            marca = ""
            if decide:
                marca = " [aceptado]" if c.codigo in aceptados else " [SIN ACEPTAR]"
            ids = sorted(set(c.ids), key=str)
            ejemplos = ids if detalle else ids[:MAX_EJEMPLOS]
            resto = "" if detalle or len(ids) <= MAX_EJEMPLOS else f" … (+{len(ids) - MAX_EJEMPLOS}, ver --detalle)"
            lineas.append(f"  {c.codigo}{marca} — {c.tabla}: {len(ids)} filas")
            lineas.append(f"      {descripcion}")
            lineas.append(f"      qué hace la copia: {accion}")
            lineas.append(f"      id: {', '.join(str(i) for i in ejemplos)}{resto}")
            if c.codigo == "LECTURA_REEMPLAZADA" and r.energia_sumada_wh.get(c.tabla):
                lineas.append(f"      energía de las lecturas reemplazadas: {r.energia_sumada_wh[c.tabla]:.3f} Wh "
                              "(se suma a la de su bloque, no se pierde)")
    return lineas


# Escritura ----------------------------------------------------------------------

class DestinoInvalido(Exception):
    pass


def _url_destino() -> str:
    from dotenv import load_dotenv
    from sqlalchemy.engine import make_url

    load_dotenv(BACKEND_DIR / ".env")
    url = os.getenv(VARIABLE_URL)
    if not url:
        raise DestinoInvalido(f"{VARIABLE_URL} no está definida (backend/.env o el entorno).")
    v1 = os.getenv("DATABASE_URL")

    def identidad(texto):
        u = make_url(texto)
        return (u.get_backend_name(), u.host, u.port, u.database, u.username)

    if v1 and identidad(v1) == identidad(url):
        raise DestinoInvalido(f"{VARIABLE_URL} apunta a la misma base que DATABASE_URL (la v1).")
    return url


def _insertar(con, tabla: str, filas: list) -> None:
    from sqlalchemy import text

    columnas = COLUMNAS_V2[tabla]
    # Los id originales se conservan: OVERRIDING SYSTEM VALUE (las columnas son ALWAYS).
    overriding = " OVERRIDING SYSTEM VALUE" if "id" in columnas else ""
    for inicio in range(0, len(filas), FILAS_POR_INSERT):
        lote = filas[inicio:inicio + FILAS_POR_INSERT]
        valores, parametros = [], {}
        for i, fila in enumerate(lote):
            nombres = []
            for j, columna in enumerate(columnas):
                nombre = f"p{i}_{j}"
                parametros[nombre] = fila[columna]
                nombres.append(f":{nombre}")
            valores.append(f"({', '.join(nombres)})")
        con.execute(
            text(f"INSERT INTO {tabla} ({', '.join(columnas)}){overriding} VALUES {', '.join(valores)}"),
            parametros,
        )


def escribir(con, r: Resultado) -> None:
    """Escribe todo en la conexión `con`, que ya está dentro de una transacción."""
    from sqlalchemy import text

    version = con.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar()
    if not version or con.execute(text("SELECT version_num FROM alembic_version")).scalar() != REVISION_V2:
        raise DestinoInvalido(f"La base destino no está en la revisión {REVISION_V2} de alembic_v2.")
    if con.execute(text("SELECT to_regclass('public.historial_acciones') IS NOT NULL")).scalar():
        raise DestinoInvalido("La base destino tiene tablas de v1.")
    for tabla in TABLAS_V2:
        if con.execute(text(f"SELECT EXISTS (SELECT 1 FROM {tabla})")).scalar():
            raise DestinoInvalido(f"La tabla {tabla} del destino no está vacía. No se escribió nada.")

    con.execute(text("SET LOCAL statement_timeout = 0"))
    for tabla in TABLAS_V2:
        _insertar(con, tabla, r.filas[tabla])
        n = con.execute(text(f"SELECT count(*) FROM {tabla}")).scalar()
        if n != len(r.filas[tabla]):
            raise DestinoInvalido(f"{tabla}: se insertaron {n} filas y se esperaban {len(r.filas[tabla])}.")
        # El siguiente id sigue al mayor copiado (o 1 si la tabla quedó vacía).
        con.execute(text(
            f"SELECT setval(pg_get_serial_sequence('public.{tabla}', 'id'), "
            f"COALESCE((SELECT max(id) FROM {tabla}), 1), (SELECT max(id) FROM {tabla}) IS NOT NULL)"
        ))
        print(f"  {tabla}: {n} filas")


def aplicar(r: Resultado) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(_url_destino(), poolclass=NullPool)
    try:
        with engine.begin() as con:
            escribir(con, r)
    finally:
        engine.dispose()


def _error_seguro(error: Exception) -> str:
    """Describe un error de la base sin su texto: Postgres puede incluir la fila
    completa (con el hash) o datos de la conexión."""
    original = getattr(error, "orig", None)
    diag = getattr(original, "diag", None)
    partes = [type(original or error).__name__]
    for etiqueta, atributo in (("código", "sqlstate"), ("tabla", "table_name"), ("restricción", "constraint_name"),
                               ("columna", "column_name")):
        valor = getattr(diag, atributo, None) if diag is not None else getattr(original, "pgcode", None) if atributo == "sqlstate" else None
        if valor:
            partes.append(f"{etiqueta}: {valor}")
    return ", ".join(partes)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Copia un respaldo de v1 a la base v2 (por defecto, ensayo).")
    parser.add_argument("carpeta", type=Path, help="carpeta respaldo_AAAAMMDD_HHMM de tools/respaldo_bd.py")
    parser.add_argument("--aplicar", action="store_true", help="escribe en DATABASE_URL_V2 (una sola transacción)")
    parser.add_argument("--aceptar", action="append", default=[],
                        help="códigos de casos aceptados, separados por comas (se puede repetir)")
    parser.add_argument("--detalle", action="store_true", help="muestra todos los id de cada caso")
    args = parser.parse_args(argv)

    aceptados = {c.strip() for valor in args.aceptar for c in valor.split(",") if c.strip()}
    desconocidos = sorted(aceptados - set(CASOS))
    if desconocidos:
        print(f"Códigos desconocidos en --aceptar: {', '.join(desconocidos)}")
        return 2

    try:
        datos, manifiesto = cargar_respaldo(args.carpeta)
    except RespaldoInvalido as error:
        print(f"Respaldo inválido: {error}")
        return 2

    if manifiesto.get("origen", "v1") != "v1":
        print(f"Respaldo inválido: es de la base {manifiesto.get('origen')}. Solo se copian respaldos de la base v1.")
        return 2

    version = manifiesto.get("version_alembic")
    print(f"Respaldo del {manifiesto.get('fecha')} — versión de Alembic v1: {version}")
    if version != VERSION_V1_ESPERADA:
        print(f"  Aviso: la copia se escribió para {VERSION_V1_ESPERADA}. Las columnas que lee sí están.")
    try:
        fecha_respaldo = datetime.fromisoformat(manifiesto["fecha"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        print("Respaldo inválido: el manifiesto no tiene una fecha legible.")
        return 2

    r = transformar(datos, fecha_respaldo)
    r.ignoradas = [t for t in datos if t not in COLUMNAS_V1 and t != "sensores"]
    for linea in informe(r, aceptados, args.detalle):
        print(linea)

    sin_aceptar = r.sin_aceptar(aceptados)
    if not args.aplicar:
        print("")
        print("ENSAYO: no se escribió nada.", end=" ")
        if sin_aceptar:
            print(f"Para aplicar hay que revisar y aceptar: --aceptar {','.join(sin_aceptar)}")
        else:
            print("Se puede aplicar con --aplicar.")
        return 0

    if sin_aceptar:
        print("")
        print(f"No se aplicó nada: faltan por aceptar {', '.join(sin_aceptar)}.")
        return 1

    print("")
    print("Aplicando en DATABASE_URL_V2 (una sola transacción)…")
    try:
        aplicar(r)
    except DestinoInvalido as error:
        print(f"No se aplicó nada: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 — cualquier fallo deshace la transacción entera
        print(f"No se aplicó nada (la transacción se deshizo): {_error_seguro(error)}")
        return 1
    print("Copia aplicada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
