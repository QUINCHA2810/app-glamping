"""
Telemetría en la tabla `mediciones` (modelo v2): una fila por domo y bloque de 10 s.

Es el único lugar que sabe cómo se reparten los temas MQTT y los POST de
/lecturas-* en las columnas de `mediciones`, y cómo se vuelven a armar las lecturas
de v1 que la API sigue devolviendo. Lo usan mqtt_listener.py, los routers
routers/lecturas_*.py, R-13, el panel, los históricos, los reportes y el generador
del domo de demostración.

Guardar (guardar_medicion) es un INSERT … ON CONFLICT (domo_id, fecha_hora) DO UPDATE:
- `fecha_hora` se trunca al inicio de su bloque de 10 s, en UTC;
- los valores instantáneos del tema se reemplazan (todos los del tema, también los que
  llegan en null: la lectura nueva reemplaza entera a la anterior de ese tema);
- la energía del intervalo (energia_fv_wh, energia_consumo_wh) se SUMA:
  COALESCE(actual, 0) + COALESCE(nueva, 0), y NULL solo si las dos son NULL. Cada
  lectura cubre su propio intervalo. Es la misma regla de tools/copiar_a_v2.py;
- las columnas de otros temas no se tocan.

Funciona igual en Postgres y en SQLite (las pruebas), con el `insert` de cada dialecto.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, case, func, or_
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from models import Medicion

BLOQUE_S = 10

# Antigüedad máxima de una medida para tratarla como ACTUAL (DB-3). La usan también la
# autonomía y el flujo promediados (servicios/energia_reciente.py), y el frontend la
# repite como DATO_VIEJO_MS (frontend/src/utils/energia.js).
# Más vieja que esto, el Panel la muestra como "sin dato" y R-13 no decide con ella. Se
# compara con el inicio del bloque, así que en la práctica la ventana es de 120 a 130 s.
VIGENCIA_S = 120
COLUMNAS_ENERGIA = ("energia_fv_wh", "energia_consumo_wh")

# El firmware dice "generando" por encima de 5 W (UMBRAL_GENERANDO_W, sensores_config.h).
# estado_generacion ya no se guarda: se calcula con este mismo umbral.
UMBRAL_GENERANDO_W = 5.0

# tema -> {campo del payload / de la API: columna de mediciones}
CAMPOS_POR_TEMA: dict[str, dict[str, str]] = {
    "bateria": {
        "estado_carga_soc": "soc_pct",
        "estado_bateria": "estado_bateria",
        "voltaje": "voltaje_bateria_v",
        "corriente": "corriente_bateria_a",
        "potencia": "potencia_bateria_w",
        "temperatura": "temperatura_bateria_c",
    },
    "fotovoltaica": {
        "voltaje": "voltaje_fv_v",
        "corriente": "corriente_fv_a",
        "potencia_generada": "potencia_fv_w",
        "energia_generada": "energia_fv_wh",
    },
    "consumo": {
        "voltaje": "voltaje_ac_v",
        "corriente": "corriente_ac_a",
        "potencia_consumida": "potencia_consumo_w",
        "energia_consumida": "energia_consumo_wh",
    },
}

# lecturas_sensores: variable_medida -> (columna, unidad que se devuelve).
VARIABLES_SENSOR: dict[str, tuple[str, Optional[str]]] = {
    "frecuencia": ("frecuencia_hz", "Hz"),
    "factor_potencia": ("factor_potencia", None),
}
TIPO_SENSOR_PZEM = "pzem004t"

COLUMNAS_POR_TEMA: dict[str, tuple[str, ...]] = {
    **{tema: tuple(campos.values()) for tema, campos in CAMPOS_POR_TEMA.items()},
    "sensores": tuple(columna for columna, _ in VARIABLES_SENSOR.values()),
}


class MedicionInvalida(ValueError):
    """La lectura no tiene dónde guardarse en v2 (p. ej. una variable de sensor sin
    columna). El mensaje es para el usuario."""


def a_utc(fecha: datetime) -> datetime:
    """Con zona y en UTC. Una fecha sin zona se toma como UTC (SQLite las devuelve así)."""
    return fecha.replace(tzinfo=timezone.utc) if fecha.tzinfo is None else fecha.astimezone(timezone.utc)


def inicio_de_bloque(fecha: datetime) -> datetime:
    """Inicio del bloque de 10 s que contiene `fecha`, en UTC."""
    segundos = math.floor(a_utc(fecha).timestamp() / BLOQUE_S) * BLOQUE_S
    return datetime.fromtimestamp(segundos, tz=timezone.utc)


def columnas_de(tema: str, valores: dict) -> dict[str, object]:
    """{columna: valor} para un tema, a partir de los campos del payload o de la API.
    Todas las columnas del tema, con None las que no vinieron. En `sensores`, solo la
    columna de su variable_medida."""
    if tema == "sensores":
        variable = valores.get("variable_medida")
        if variable not in VARIABLES_SENSOR:
            raise MedicionInvalida(
                f"variable_medida debe ser una de: {', '.join(VARIABLES_SENSOR)} (las demás no tienen columna en v2)."
            )
        return {VARIABLES_SENSOR[variable][0]: valores.get("valor")}
    return {columna: valores.get(campo) for campo, columna in CAMPOS_POR_TEMA[tema].items()}


def _insert(db: Session):
    return postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert


def guardar_medicion(
    db: Session, domo_id: int, tema: str, valores: dict, fecha_hora: Optional[datetime] = None
) -> datetime:
    """Escribe la lectura de un tema en el bloque que le toca (ver el docstring del
    módulo) y devuelve el inicio de ese bloque. No hace commit: es de quien llama."""
    columnas = columnas_de(tema, valores)
    bloque = inicio_de_bloque(fecha_hora or datetime.now(timezone.utc))
    tabla = Medicion.__table__
    sentencia = _insert(db)(tabla).values(domo_id=domo_id, fecha_hora=bloque, **columnas)
    nuevos = sentencia.excluded
    actualizar = {}
    for columna in columnas:
        if columna in COLUMNAS_ENERGIA:
            actual, nueva = tabla.c[columna], nuevos[columna]
            actualizar[columna] = case(
                (and_(actual.is_(None), nueva.is_(None)), None),
                else_=func.coalesce(actual, 0.0) + func.coalesce(nueva, 0.0),
            )
        else:
            actualizar[columna] = nuevos[columna]
    db.execute(sentencia.on_conflict_do_update(index_elements=["domo_id", "fecha_hora"], set_=actualizar))
    return bloque


def medicion_del_bloque(db: Session, domo_id: int, bloque: datetime) -> Optional[Medicion]:
    return db.query(Medicion).filter(Medicion.domo_id == domo_id, Medicion.fecha_hora == bloque).first()


# Lectura (v1) a partir de una medición ------------------------------------------

def tiene_datos(tema: str):
    """Condición SQL: la fila tiene algún dato de ese tema."""
    return or_(*(getattr(Medicion, columna).is_not(None) for columna in COLUMNAS_POR_TEMA[tema]))


def consulta_de(db: Session, domo_id: int, tema: str, fecha_desde=None, fecha_hasta=None):
    consulta = db.query(Medicion).filter(Medicion.domo_id == domo_id, tiene_datos(tema))
    if fecha_desde is not None:
        consulta = consulta.filter(Medicion.fecha_hora >= fecha_desde)
    if fecha_hasta is not None:
        consulta = consulta.filter(Medicion.fecha_hora <= fecha_hasta)
    return consulta.order_by(Medicion.fecha_hora.desc(), Medicion.id.desc())


def ultima(db: Session, domo_id: int, tema: str) -> Optional[Medicion]:
    """La medición más reciente con algún dato de ese tema (la "última lectura" de v1)."""
    return consulta_de(db, domo_id, tema).first()


def ahora_utc() -> datetime:
    """El reloj de la vigencia (las pruebas lo reemplazan)."""
    return datetime.now(timezone.utc)


def vigente_desde(ahora: Optional[datetime] = None) -> datetime:
    """Desde cuándo (UTC) una medida cuenta como actual."""
    return a_utc(ahora or ahora_utc()) - timedelta(seconds=VIGENCIA_S)


def ultimo_valor(
    db: Session, domo_id: int, columna: str, desde: Optional[datetime] = None
) -> Optional[tuple[object, datetime]]:
    """(valor, fecha_hora) del último dato NO nulo de una columna, o None. Con `desde`,
    solo si es de ese instante en adelante (ver vigente_desde). Una fila posterior con
    esa columna en null (p. ej. un ESP32 sin sensor que publica null) no lo tapa: por
    eso quien lo presenta como actual tiene que pasar `desde`."""
    valor = getattr(Medicion, columna)
    consulta = db.query(valor, Medicion.fecha_hora).filter(Medicion.domo_id == domo_id, valor.is_not(None))
    if desde is not None:
        consulta = consulta.filter(Medicion.fecha_hora >= desde)
    fila = consulta.order_by(Medicion.fecha_hora.desc()).first()
    return None if fila is None else (fila[0], a_utc(fila[1]))


def estado_generacion_de(potencia_fv_w: Optional[float]) -> Optional[str]:
    if potencia_fv_w is None:
        return None
    return "generando" if potencia_fv_w > UMBRAL_GENERANDO_W else "sin_generacion"


def como_lectura(medicion: Medicion, tema: str) -> dict:
    """La lectura de v1 de un tema (sin sensores), con los nombres de la API."""
    salida = {"id": medicion.id, "domo_id": medicion.domo_id, "sensor_id": None, "fecha_hora": medicion.fecha_hora}
    for campo, columna in CAMPOS_POR_TEMA[tema].items():
        salida[campo] = getattr(medicion, columna)
    if tema == "fotovoltaica":
        salida["estado_generacion"] = estado_generacion_de(medicion.potencia_fv_w)
    if tema == "consumo":
        salida["dispositivo_id"] = None
    return salida


def como_lecturas_de_sensor(medicion: Medicion) -> list[dict]:
    """Una lectura de sensor por cada variable con dato en la fila."""
    return [
        {
            "id": medicion.id,
            "domo_id": medicion.domo_id,
            "sensor_id": None,
            "tipo_sensor": TIPO_SENSOR_PZEM,
            "variable_medida": variable,
            "valor": getattr(medicion, columna),
            "unidad_medida": unidad,
            "estado": None,
            "fecha_hora": medicion.fecha_hora,
        }
        for variable, (columna, unidad) in VARIABLES_SENSOR.items()
        if getattr(medicion, columna) is not None
    ]

