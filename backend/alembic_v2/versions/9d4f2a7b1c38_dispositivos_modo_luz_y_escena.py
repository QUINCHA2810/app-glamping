"""dispositivos: modo de luz y escena del bombillo

Control completo del bombillo (firmware/domo_nodo: modos blanco, color y escena). Dos
columnas opcionales en `dispositivos`:

- `modo_luz`: 'blanco' | 'color' | 'escena'; NULL = sin modo (el bombillo solo recibe
  encendido y brillo, como hasta ahora).
- `escena`: el nombre de la escena elegida. Sus valores válidos NO van en un CHECK: el
  único lugar con la lista es servicios/iluminacion.py, que coincide con el firmware.

Se reutilizan `brillo`, `color` (ahora también "#rrggbb") y `temperatura_color`
(calida | neutra | fria); esas columnas son TEXT sin CHECK, así que no cambian.

CHECK nuevos: `modo_luz` en la lista, y las dos columnas solo en bombillos (como brillo).
Ninguna fila existente los incumple: las columnas nacen en NULL.

Solo agrega columnas opcionales: no toca datos y es instantánea.

downgrade() las elimina, con lo que se pierden el modo y la escena guardados.

Revision ID: 9d4f2a7b1c38
Revises: 4e8b2c6d9f17
Create Date: 2026-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9d4f2a7b1c38'
down_revision: Union[str, Sequence[str], None] = '4e8b2c6d9f17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copia congelada de servicios.iluminacion.MODOS_LUZ (la compara tests/test_iluminacion.py).
MODOS_LUZ = ('blanco', 'color', 'escena')


def upgrade() -> None:
    op.add_column('dispositivos', sa.Column('modo_luz', sa.Text(), nullable=True))
    op.add_column('dispositivos', sa.Column('escena', sa.Text(), nullable=True))
    op.create_check_constraint(
        'dispositivos_modo_luz_check', 'dispositivos',
        f"modo_luz IN ({', '.join(repr(m) for m in MODOS_LUZ)})",
    )
    op.create_check_constraint(
        'dispositivos_luz_solo_bombillo_check', 'dispositivos',
        "tipo = 'bombillo' OR (modo_luz IS NULL AND escena IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint('dispositivos_luz_solo_bombillo_check', 'dispositivos', type_='check')
    op.drop_constraint('dispositivos_modo_luz_check', 'dispositivos', type_='check')
    op.drop_column('dispositivos', 'escena')
    op.drop_column('dispositivos', 'modo_luz')
