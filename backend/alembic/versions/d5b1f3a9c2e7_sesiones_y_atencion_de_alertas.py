"""versión de sesión de los usuarios y fecha de atención de las alertas

1. usuarios.version_sesion: entero que viaja en el JWT. Subirlo invalida todas las
   sesiones abiertas de esa cuenta, algo que no se puede hacer de otra forma porque
   los tokens no se guardan en ninguna parte. Las filas existentes quedan en 0, igual
   que los tokens ya emitidos, que no traen el dato: siguen siendo válidos.
2. alertas.fecha_atendida: cuándo pasó la alerta a 'atendida'. Sin ella no se puede
   calcular el tiempo medio de atención. Las alertas ya atendidas quedan en NULL
   porque ese instante no se registró, y por eso no cuentan en ese promedio.

Migración escrita a mano (sin --autogenerate), ver backend/README.md. Solo agrega
columnas, sin tocar datos.

downgrade() las elimina. Se pierden las fechas de atención y, como los tokens dejan
de compararse, una sesión cerrada por un restablecimiento volvería a servir hasta que
venza.

Revision ID: d5b1f3a9c2e7
Revises: c4a8e2f7d1b6
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5b1f3a9c2e7'
down_revision: Union[str, Sequence[str], None] = 'c4a8e2f7d1b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('usuarios', sa.Column('version_sesion', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('alertas', sa.Column('fecha_atendida', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('alertas', 'fecha_atendida')
    op.drop_column('usuarios', 'version_sesion')
