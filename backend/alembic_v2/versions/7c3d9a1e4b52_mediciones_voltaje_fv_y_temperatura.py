"""mediciones: voltaje de los paneles y temperatura de la batería

Toda medida que publiquen el firmware o el simulador en domo/{id}/telemetria/* tiene su
columna en `mediciones` (ronda DB-2, tarea 0), aunque ninguna pantalla la muestre todavía:
son datos para el documento de grado y para Power BI.

- `voltaje_fv_v`: campo `voltaje` del tema fotovoltaica. El firmware lo manda siempre en
  null (el GPIO33 quedó libre para medirlo); el simulador sí lo manda.
- `temperatura_bateria_c`: campo `temperatura` del tema batería. El firmware no lo manda
  (no hay sensor); el simulador y el generador del domo de demostración sí.

Rangos iguales a schemas.LIMITES_TELEMETRIA (`voltaje`, `temperatura`), que el listener ya
aplica guardando NULL fuera de ellos: ninguna lectura que hoy se guarda sería rechazada.

Solo agrega columnas opcionales: no toca datos y es instantánea.

downgrade() las elimina, con lo que se pierden esos valores.

Revision ID: 7c3d9a1e4b52
Revises: 2b8e4f1a6c90
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7c3d9a1e4b52'
down_revision: Union[str, Sequence[str], None] = '2b8e4f1a6c90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# columna -> (mínimo, máximo), los de schemas.LIMITES_TELEMETRIA. Copia congelada: la
# comparan tests/test_copiar_a_v2.py y tests/test_modelos_v2.py.
RANGOS_NUEVOS = {
    'voltaje_fv_v': (-10.0, 1000.0),
    'temperatura_bateria_c': (-50.0, 150.0),
}


def upgrade() -> None:
    for columna, (minimo, maximo) in RANGOS_NUEVOS.items():
        op.add_column('mediciones', sa.Column(columna, sa.Double(), nullable=True))
        op.create_check_constraint(
            f'mediciones_{columna}_rango', 'mediciones', f'{columna} BETWEEN {minimo!r} AND {maximo!r}'
        )


def downgrade() -> None:
    for columna in reversed(list(RANGOS_NUEVOS)):
        op.drop_constraint(f'mediciones_{columna}_rango', 'mediciones', type_='check')
        op.drop_column('mediciones', columna)
