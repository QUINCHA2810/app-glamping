"""fecha de desactivación, contraseña temporal y descripciones de categoría

1. dispositivos.desactivado_en: cuándo se desactivó el dispositivo, o NULL si está
   activo. Termina el tiempo que una toma cuenta como "apagada por el sistema" en los
   reportes. Para los que ya estaban inactivos, que no lo registraron, se usa la fecha
   de su última acción en el historial: es lo último que se sabe de ellos, y la
   desactivación fue después. Si una toma se desactivó mientras R-13 la tenía cortada,
   ese corte queda en 0 horas en vez de crecer sin fin. Sin ninguna acción en el
   historial, el momento de esta migración.
2. usuarios.debe_cambiar_contrasena: la cuenta tiene una contraseña temporal y, hasta
   que la cambie, el backend solo le deja ver su perfil y cambiarla. Las cuentas que ya
   existen quedan en false: nadie queda bloqueado al aplicar esta migración.
3. Las descripciones del catálogo categoria_dispositivo ya no hablan de puntos fijos:
   desde los perfiles de control, los umbrales dependen de la batería y del perfil.

Migración escrita a mano (sin --autogenerate), ver backend/README.md.

downgrade() elimina las dos columnas, con lo que se pierden las fechas de desactivación
y se libera de la obligación de cambio a quien la tenga, y restaura las descripciones
anteriores.

Revision ID: e6c2a8d4f1b9
Revises: d5b1f3a9c2e7
Create Date: 2026-11-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6c2a8d4f1b9'
down_revision: Union[str, Sequence[str], None] = 'd5b1f3a9c2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DESCRIPCIONES_NUEVAS = {
    'esencial': 'Lo que no puede faltar: el sistema nunca la apaga. El apagado manual sigue permitido',
    'importante': 'Aguanta hasta la reserva de la batería; es lo último que el sistema apaga',
    'confort': 'Se apaga antes que lo importante, para que eso dure más',
    'prescindible': 'Lo primero que el sistema apaga cuando baja la batería',
}
DESCRIPCIONES_ANTERIORES = {
    'esencial': 'R-13 nunca la corta; el apagado manual sigue permitido',
    'importante': 'Se corta al llegar a la reserva de la batería',
    'confort': 'Se corta 15 puntos por encima de la reserva de la batería',
    'prescindible': 'Se corta 30 puntos por encima de la reserva de la batería',
}


def _poner_descripciones(descripciones: dict) -> None:
    for codigo, descripcion in descripciones.items():
        op.execute(
            sa.text('UPDATE categoria_dispositivo SET descripcion = :d WHERE codigo = :c').bindparams(d=descripcion, c=codigo)
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('dispositivos', sa.Column('desactivado_en', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE dispositivos AS d
        SET desactivado_en = COALESCE(
            (SELECT max(h.fecha_hora) FROM historial_acciones AS h WHERE h.dispositivo_id = d.id),
            now()
        )
        WHERE NOT d.activo
        """
    )
    op.add_column(
        'usuarios', sa.Column('debe_cambiar_contrasena', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    _poner_descripciones(DESCRIPCIONES_NUEVAS)


def downgrade() -> None:
    """Downgrade schema."""
    _poner_descripciones(DESCRIPCIONES_ANTERIORES)
    op.drop_column('usuarios', 'debe_cambiar_contrasena')
    op.drop_column('dispositivos', 'desactivado_en')
