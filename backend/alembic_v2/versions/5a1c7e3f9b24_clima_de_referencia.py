"""clima de referencia: coordenadas del domo y tabla clima (Open-Meteo)

Para explicar la generación solar con el clima del lugar:

- `domos.latitud` y `domos.longitud`: opcionales. Un domo sin coordenadas no tiene clima.
  Las dos van juntas (o las dos NULL) y en rango (-90..90, -180..180).
- Tabla `clima`: una fila por domo y hora de la consulta de Open-Meteo (`current`), con
  UNIQUE (domo_id, fecha_hora): la misma consulta repetida no duplica filas. Son
  estimaciones del modelo meteorológico para las coordenadas, NO mediciones en el domo.
  La escribe el listener (servicios/clima.py) cada 15 min.
- RLS activado y sin políticas en `clima`, igual que las demás tablas (2b8e4f1a6c90): el
  backend entra como dueño, y la API de datos de Supabase no ve nada. Las pruebas en
  SQLite no pasan por las migraciones (crean el esquema con models.py), así que no les
  afecta.

Solo agrega columnas opcionales y una tabla nueva: no toca datos.

downgrade() elimina la tabla CON SUS DATOS y las coordenadas.

Revision ID: 5a1c7e3f9b24
Revises: 9d4f2a7b1c38
Create Date: 2026-11-22 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5a1c7e3f9b24'
down_revision: Union[str, Sequence[str], None] = '9d4f2a7b1c38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copia congelada de las reglas de models.Clima (la compara tests/test_clima.py).
RANGOS_CLIMA = {
    'nubosidad_pct': (0, 100),
    'radiacion_w_m2': (0, 2000),
    'precipitacion_mm': (0, 500),
    'temperatura_c': (-90, 70),
}

_CHECKS = ',\n    '.join(
    f'CONSTRAINT clima_{columna}_rango CHECK ({columna} BETWEEN {minimo} AND {maximo})'
    for columna, (minimo, maximo) in RANGOS_CLIMA.items()
)

CLIMA = f"""
CREATE TABLE clima (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domo_id          BIGINT NOT NULL REFERENCES domos (id) ON DELETE CASCADE,
    fecha_hora       TIMESTAMPTZ NOT NULL,
    temperatura_c    DOUBLE PRECISION,
    nubosidad_pct    DOUBLE PRECISION,
    radiacion_w_m2   DOUBLE PRECISION,
    precipitacion_mm DOUBLE PRECISION,
    codigo_clima     SMALLINT,
    es_de_dia        BOOLEAN,
    fuente           TEXT NOT NULL DEFAULT 'open-meteo',
    CONSTRAINT clima_domo_fecha_key UNIQUE (domo_id, fecha_hora),
    CONSTRAINT clima_codigo_clima_rango CHECK (codigo_clima BETWEEN 0 AND 99),
    {_CHECKS}
)
"""


def upgrade() -> None:
    op.add_column('domos', sa.Column('latitud', sa.Float(precision=53), nullable=True))
    op.add_column('domos', sa.Column('longitud', sa.Float(precision=53), nullable=True))
    op.create_check_constraint(
        'domos_coordenadas_check', 'domos',
        '(latitud IS NULL AND longitud IS NULL) OR '
        '(latitud IS NOT NULL AND longitud IS NOT NULL'
        ' AND latitud BETWEEN -90 AND 90 AND longitud BETWEEN -180 AND 180)',
    )

    # La UNIQUE (domo_id, fecha_hora) ya es el índice de "la última" y de los rangos.
    op.execute(CLIMA)
    op.execute('ALTER TABLE clima ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table('clima')
    op.drop_constraint('domos_coordenadas_check', 'domos', type_='check')
    op.drop_column('domos', 'longitud')
    op.drop_column('domos', 'latitud')
