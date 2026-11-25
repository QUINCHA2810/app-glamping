"""esquema base del modelo v2: 7 tablas en árbol, sin catálogos

Base: docs/modelo_v2/esquema_prueba.sql (versión A de mediciones). Las diferencias con
ese archivo y su porqué están en docs/modelo_v2/MAPEO.md, sección "Esquema v2".

    usuarios ──< asignaciones >── domos ──< dispositivos ──< acciones
                                    ├──< alertas
                                    └──< mediciones

Entre dos tablas hay un solo camino de claves foráneas. Los catálogos son CHECK.

- Tipos: TEXT para los textos libres (v1 tampoco limita su largo y un VARCHAR más corto
  haría fallar con 500 una entrada que hoy se acepta) y double precision para las
  medidas (v1 usa `real`; el código las lee como float). Los rangos de mediciones son
  los de schemas.LIMITES_TELEMETRIA, que el listener ya aplica (guarda NULL fuera de
  ellos): así ninguna lectura que hoy se guarda sería rechazada. Un CHECK con BETWEEN
  rechaza también NaN e Infinity (en Postgres NaN es mayor que cualquier número).
- RLS activado y sin políticas en todas las tablas, igual que en v1: el backend entra
  como `postgres` (dueño y con BYPASSRLS), y la API de datos de Supabase no ve nada.
- El esquema `bi` y el rol `lector_bi` son de la ronda DB-3, no de esta revisión.

Antes de crear nada comprueba que la base no tenga tablas del modelo v1: si
DATABASE_URL_V2 apuntara por error a la base actual, falla sin tocar nada.

downgrade() elimina las 7 tablas CON SUS DATOS. Solo para una base de pruebas.

Revision ID: 2b8e4f1a6c90
Revises:
Create Date: 2026-11-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = '2b8e4f1a6c90'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Valores de los CHECK. tools/copiar_a_v2.py valida con los mismos (tests/test_copiar_a_v2.py
# lo comprueba); son copias congeladas: una migración no importa código que pueda cambiar.
TIPOS_BATERIA = ('agm', 'lifepo4')
PERFILES = ('conservador', 'equilibrado', 'maximo_uso')
ROLES_ASIGNACION = ('dueno', 'administrador')
TIPOS_DISPOSITIVO = ('toma', 'bombillo')
CATEGORIAS = ('esencial', 'importante', 'confort', 'prescindible')
ESTADOS_DISPOSITIVO = ('encendido', 'apagado')
ACCIONES = ('encender', 'apagar')
ACTORES = ('sistema', 'dueno', 'administrador')
TIPOS_ALERTA = ('bateria_baja', 'consumo_elevado', 'generacion_nula', 'dispositivo_desconectado', 'sobrecarga')
NIVELES_ALERTA = ('informativo', 'advertencia', 'critico')
ESTADOS_ALERTA = ('activa', 'atendida', 'cerrada')
ESTADOS_BATERIA = ('cargando', 'descargando', 'cargada', 'en_reposo')
CAPACIDAD_MAXIMA_WH = 10_000_000

# columna de mediciones -> (mínimo, máximo), los de schemas.LIMITES_TELEMETRIA.
RANGOS_MEDICIONES = {
    'soc_pct': (0.0, 100.0),
    'voltaje_bateria_v': (-10.0, 1000.0),
    'corriente_bateria_a': (-1000.0, 1000.0),
    'potencia_bateria_w': (-100_000.0, 100_000.0),
    'corriente_fv_a': (-1000.0, 1000.0),
    'potencia_fv_w': (-100.0, 100_000.0),
    'energia_fv_wh': (0.0, 1e9),
    'voltaje_ac_v': (-10.0, 1000.0),
    'corriente_ac_a': (-1000.0, 1000.0),
    'potencia_consumo_w': (-100.0, 100_000.0),
    'energia_consumo_wh': (0.0, 1e9),
    'frecuencia_hz': (-1e9, 1e9),
    'factor_potencia': (-1e9, 1e9),
}

TABLAS = ('usuarios', 'domos', 'asignaciones', 'dispositivos', 'acciones', 'alertas', 'mediciones')

# Si existe alguna, DATABASE_URL_V2 apunta a la base v1.
TABLAS_V1 = ('historial_acciones', 'lecturas_bateria', 'administrador_domo', 'configuracion_sistema')


def _en(columna: str, valores) -> str:
    return f"{columna} IN ({', '.join(repr(v) for v in valores)})"


def _rango(columna: str, minimo: float, maximo: float) -> str:
    return f"CONSTRAINT mediciones_{columna}_rango CHECK ({columna} BETWEEN {minimo!r} AND {maximo!r})"


GUARDA = f"""
DO $$
BEGIN
    IF {' OR '.join(f"to_regclass('public.{t}') IS NOT NULL" for t in TABLAS_V1)} THEN
        RAISE EXCEPTION 'Esta base tiene tablas del modelo v1: DATABASE_URL_V2 no apunta a una base v2 vacía. No se creó nada.';
    END IF;
END
$$
"""

USUARIOS = """
CREATE TABLE usuarios (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre                  TEXT NOT NULL,
    apellido                TEXT,
    correo                  TEXT NOT NULL,
    contrasena_hash         TEXT NOT NULL,
    telefono                TEXT,
    activo                  BOOLEAN NOT NULL DEFAULT true,
    debe_cambiar_contrasena BOOLEAN NOT NULL DEFAULT false,
    version_sesion          INTEGER NOT NULL DEFAULT 0,
    creado_en               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT usuarios_correo_key UNIQUE (correo),
    CONSTRAINT usuarios_version_sesion_check CHECK (version_sesion >= 0)
)
"""

DOMOS = f"""
CREATE TABLE domos (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre               TEXT NOT NULL,
    ubicacion            TEXT,
    es_demostracion      BOOLEAN NOT NULL DEFAULT false,
    bateria_tipo         TEXT NOT NULL DEFAULT 'lifepo4',
    bateria_capacidad_wh DOUBLE PRECISION,
    perfil               TEXT NOT NULL DEFAULT 'conservador',
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT domos_bateria_tipo_check CHECK ({_en('bateria_tipo', TIPOS_BATERIA)}),
    CONSTRAINT domos_bateria_capacidad_check CHECK (bateria_capacidad_wh > 0 AND bateria_capacidad_wh <= {CAPACIDAD_MAXIMA_WH}),
    CONSTRAINT domos_perfil_check CHECK ({_en('perfil', PERFILES)})
)
"""

ASIGNACIONES = f"""
CREATE TABLE asignaciones (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id BIGINT NOT NULL REFERENCES usuarios (id) ON DELETE RESTRICT,
    domo_id    BIGINT NOT NULL REFERENCES domos (id) ON DELETE CASCADE,
    rol        TEXT NOT NULL,
    CONSTRAINT asignaciones_usuario_domo_key UNIQUE (usuario_id, domo_id),
    CONSTRAINT asignaciones_rol_check CHECK ({_en('rol', ROLES_ASIGNACION)})
)
"""

DISPOSITIVOS = f"""
CREATE TABLE dispositivos (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domo_id             BIGINT NOT NULL REFERENCES domos (id) ON DELETE RESTRICT,
    nombre              TEXT NOT NULL,
    tipo                TEXT NOT NULL,
    categoria           TEXT NOT NULL,
    id_fisico           TEXT,
    ubicacion           TEXT,
    estado              TEXT NOT NULL DEFAULT 'apagado',
    brillo              SMALLINT,
    color               TEXT,
    temperatura_color   TEXT,
    activo              BOOLEAN NOT NULL DEFAULT true,
    desactivado_en      TIMESTAMPTZ,
    conectado           BOOLEAN NOT NULL DEFAULT false,
    ultima_comunicacion TIMESTAMPTZ,
    CONSTRAINT dispositivos_domo_id_id_fisico_key UNIQUE (domo_id, id_fisico),
    CONSTRAINT dispositivos_tipo_check CHECK ({_en('tipo', TIPOS_DISPOSITIVO)}),
    CONSTRAINT dispositivos_categoria_check CHECK ({_en('categoria', CATEGORIAS)}),
    CONSTRAINT dispositivos_estado_check CHECK ({_en('estado', ESTADOS_DISPOSITIVO)}),
    CONSTRAINT dispositivos_ubicacion_largo_check CHECK (char_length(ubicacion) <= 100),
    CONSTRAINT dispositivos_brillo_rango_check CHECK (brillo BETWEEN 0 AND 100),
    CONSTRAINT dispositivos_brillo_solo_bombillo_check CHECK (tipo = 'bombillo' OR brillo IS NULL),
    CONSTRAINT dispositivos_bombillo_esencial_check CHECK (tipo <> 'bombillo' OR categoria = 'esencial'),
    CONSTRAINT dispositivos_desactivado_en_check CHECK (activo = (desactivado_en IS NULL))
)
"""

ACCIONES_SQL = f"""
CREATE TABLE acciones (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dispositivo_id  BIGINT NOT NULL REFERENCES dispositivos (id) ON DELETE RESTRICT,
    accion          TEXT NOT NULL,
    estado_anterior TEXT NOT NULL,
    estado_nuevo    TEXT NOT NULL,
    actor_tipo      TEXT NOT NULL,
    actor_ref       TEXT NOT NULL,
    condicion       TEXT,
    resultado       TEXT NOT NULL,
    fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT acciones_accion_check CHECK ({_en('accion', ACCIONES)}),
    CONSTRAINT acciones_estado_anterior_check CHECK ({_en('estado_anterior', ESTADOS_DISPOSITIVO)}),
    CONSTRAINT acciones_estado_nuevo_check CHECK ({_en('estado_nuevo', ESTADOS_DISPOSITIVO)}),
    CONSTRAINT acciones_actor_tipo_check CHECK ({_en('actor_tipo', ACTORES)})
)
"""

ALERTAS = f"""
CREATE TABLE alertas (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domo_id         BIGINT NOT NULL REFERENCES domos (id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL,
    nivel           TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'activa',
    variable        TEXT,
    valor_detectado DOUBLE PRECISION,
    limite          DOUBLE PRECISION,
    mensaje         TEXT NOT NULL,
    id_fisico_ref   TEXT,
    creada_por_r13  BOOLEAN NOT NULL DEFAULT false,
    fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_atendida  TIMESTAMPTZ,
    CONSTRAINT alertas_tipo_check CHECK ({_en('tipo', TIPOS_ALERTA)}),
    CONSTRAINT alertas_nivel_check CHECK ({_en('nivel', NIVELES_ALERTA)}),
    CONSTRAINT alertas_estado_check CHECK ({_en('estado', ESTADOS_ALERTA)}),
    CONSTRAINT alertas_atendida_con_fecha_check CHECK (estado <> 'atendida' OR fecha_atendida IS NOT NULL)
)
"""

_COLUMNAS_MEDICIONES = ',\n    '.join(f'{columna} DOUBLE PRECISION' for columna in RANGOS_MEDICIONES)
_CHECKS_MEDICIONES = ',\n    '.join(_rango(c, mn, mx) for c, (mn, mx) in RANGOS_MEDICIONES.items())

MEDICIONES = f"""
CREATE TABLE mediciones (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domo_id        BIGINT NOT NULL REFERENCES domos (id) ON DELETE CASCADE,
    fecha_hora     TIMESTAMPTZ NOT NULL,
    estado_bateria TEXT,
    {_COLUMNAS_MEDICIONES},
    CONSTRAINT mediciones_domo_fecha_key UNIQUE (domo_id, fecha_hora),
    CONSTRAINT mediciones_estado_bateria_check CHECK ({_en('estado_bateria', ESTADOS_BATERIA)}),
    {_CHECKS_MEDICIONES}
)
"""

INDICES = (
    # Un solo dueño por domo. "Al menos uno" no se puede exigir con un CHECK: lo
    # garantiza el backend al crear el domo (y lo revisa tools/copiar_a_v2.py).
    "CREATE UNIQUE INDEX asignaciones_un_dueno_por_domo ON asignaciones (domo_id) WHERE rol = 'dueno'",
    "CREATE INDEX asignaciones_domo_idx ON asignaciones (domo_id)",
    "CREATE INDEX dispositivos_domo_idx ON dispositivos (domo_id)",
    # La última acción de cada toma (R-13) y el historial por dispositivo.
    "CREATE INDEX acciones_dispositivo_fecha_idx ON acciones (dispositivo_id, fecha_hora DESC, id DESC)",
    "CREATE INDEX alertas_domo_fecha_idx ON alertas (domo_id, fecha_hora DESC)",
    # mediciones: la UNIQUE (domo_id, fecha_hora) ya es el índice de las consultas por rango.
)


def upgrade() -> None:
    op.execute(GUARDA)
    for sql in (USUARIOS, DOMOS, ASIGNACIONES, DISPOSITIVOS, ACCIONES_SQL, ALERTAS, MEDICIONES):
        op.execute(sql)
    for sql in INDICES:
        op.execute(sql)
    for tabla in (*TABLAS, 'alembic_version'):
        op.execute(f'ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    # Las hojas primero: ninguna FK queda colgando. alembic_version es de Alembic y
    # conserva su RLS.
    for tabla in reversed(TABLAS):
        op.execute(f'DROP TABLE {tabla}')
