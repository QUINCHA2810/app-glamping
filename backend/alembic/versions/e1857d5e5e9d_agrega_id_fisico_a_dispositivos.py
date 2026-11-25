"""agrega id_fisico a dispositivos

Agrega `dispositivos.id_fisico` (Text, nullable): el identificador del
dispositivo FÍSICO real que representa la fila (por ejemplo, la toma
Tuya que el ESP32 conmuta dentro del domo).

NULL = dispositivo SIMULADO. Esa es la razón de que la columna sea
nullable y no tenga server_default: todas las filas que ya existen en
`dispositivos` quedan en NULL, es decir, siguen siendo simuladas y
ningún cambio de estado suyo se publica por MQTT (ver
backend/mqtt_publisher.py). Solo las filas a las que se les asigne un
`id_fisico` a mano empiezan a mover hardware real.

Migración escrita a mano (sin --autogenerate) a propósito: es un único
add_column conocido, y autogenerate contra esta base de datos arrastraría
diferencias no relacionadas (ver backend/README.md, sección
"Migraciones" — el esquema se viene aplicando directamente en Supabase).

Revision ID: e1857d5e5e9d
Revises: 04087f548858
Create Date: 2026-10-14 21:32:09.972597

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1857d5e5e9d'
down_revision: Union[str, Sequence[str], None] = '04087f548858'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('dispositivos', sa.Column('id_fisico', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('dispositivos', 'id_fisico')
