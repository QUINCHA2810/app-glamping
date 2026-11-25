"""domos.es_demostracion: domo con datos sintéticos

Un domo de demostración no corresponde a un domo real: sus lecturas, acciones y
alertas las genera tools/generar_datos_demo.py. La app lo marca siempre como
"Demostración · datos sintéticos", y el CSV de Reportes y la exportación para Power BI
llevan la misma marca. Ningún endpoint la cambia: solo la pone el generador.

Los domos que ya existen quedan en false.

Migración escrita a mano (sin --autogenerate), ver backend/README.md.

downgrade() elimina la columna. Los domos de demostración que existan dejan de estar
marcados y pasan a verse como domos normales: bórralos o renómbralos antes.

Revision ID: f7d3b9e5a2c8
Revises: e6c2a8d4f1b9
Create Date: 2026-11-05 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7d3b9e5a2c8'
down_revision: Union[str, Sequence[str], None] = 'e6c2a8d4f1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('domos', sa.Column('es_demostracion', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('domos', 'es_demostracion')
