"""categorías de dispositivo, fase 2 de integridad

En este orden (todo en una sola transacción: si algo falla, no queda nada):

1. Catálogo categoria_dispositivo, con el texto como clave primaria, igual que
   los de la fase 1, y con RLS sin políticas.
2. dispositivos.categoria con FK, rellena y luego NOT NULL:
   - lo que no es toma (bombillos): esencial;
   - tomas con umbrales: la categoría cuyo preset LiFePO4 esté más cerca, por
     suma de las dos distancias; si hay empate, la que protege más. Los umbrales
     no se tocan;
   - tomas sin umbrales: confort. Siguen con los globales del domo (por defecto
     30/40), de los que confort es el preset más cercano.
3. dispositivos.prioridad pasa a ser la derivada de la categoría, porque R-13
   ordena por ella y la API ya no permite fijarla.
4. dispositivos.ubicacion y dispositivos.activo.
5. CHECK de umbrales: rango 0-100, ambos o ninguno, y reactivación >= apagado + 5
   en lugar de reactivación > apagado. Este último compara en numeric porque las
   columnas son `real`: comparadas así, bandas exactas de 5 como 0.1/5.1 fallan
   por precisión (52 de las 951 combinaciones posibles con un decimal).

Si alguna fila incumple un CHECK nuevo, la migración falla y no cambia nada: hay
que corregir esa fila antes (ver la consulta de revisión previa).

Los presets están copiados aquí a propósito, no importados de
servicios/categorias.py: una migración debe seguir haciendo lo mismo aunque esos
valores cambien después.

Migración escrita a mano (sin --autogenerate), ver backend/README.md.

downgrade() deja el esquema como estaba, pero PIERDE categoria, ubicacion y
activo (los desactivados vuelven a contar como dispositivos normales) y NO
recupera la prioridad anterior de cada dispositivo.

Revision ID: c4a8e2f7d1b6
Revises: b7d2c4e8a1f3
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a8e2f7d1b6'
down_revision: Union[str, Sequence[str], None] = 'b7d2c4e8a1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CATEGORIAS = [
    {'codigo': 'esencial', 'descripcion': 'R-13 nunca la corta; el apagado manual sigue permitido'},
    {'codigo': 'importante', 'descripcion': 'Se corta al llegar a la reserva de la batería'},
    {'codigo': 'confort', 'descripcion': 'Se corta 15 puntos por encima de la reserva de la batería'},
    {'codigo': 'prescindible', 'descripcion': 'Se corta 30 puntos por encima de la reserva de la batería'},
]

CHECK_NUEVOS = [
    ('dispositivos_umbral_apagado_rango_check', 'umbral_apagado_soc >= 0 AND umbral_apagado_soc <= 100'),
    ('dispositivos_umbral_reactivacion_rango_check', 'umbral_reactivacion_soc >= 0 AND umbral_reactivacion_soc <= 100'),
    ('dispositivos_umbrales_par_check', '(umbral_apagado_soc IS NULL) = (umbral_reactivacion_soc IS NULL)'),
    ('dispositivos_umbrales_soc_check', 'CAST(umbral_reactivacion_soc AS numeric) >= CAST(umbral_apagado_soc AS numeric) + 5'),
]


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Catálogo
    catalogo = op.create_table(
        'categoria_dispositivo',
        sa.Column('codigo', sa.Text(), primary_key=True),
        sa.Column('descripcion', sa.Text(), nullable=True),
    )
    op.bulk_insert(catalogo, CATEGORIAS)
    op.execute('ALTER TABLE categoria_dispositivo ENABLE ROW LEVEL SECURITY')

    # 2. Categoría. "orden": en un empate gana la que protege más.
    op.add_column('dispositivos', sa.Column('categoria', sa.Text(), nullable=True))
    op.create_foreign_key(
        'dispositivos_categoria_fkey', 'dispositivos', 'categoria_dispositivo', ['categoria'], ['codigo']
    )
    op.execute(
        """
        UPDATE dispositivos AS d
        SET categoria = CASE
            WHEN d.tipo <> 'toma' THEN 'esencial'
            WHEN d.umbral_apagado_soc IS NULL OR d.umbral_reactivacion_soc IS NULL THEN 'confort'
            ELSE (
                SELECT p.codigo
                FROM (VALUES
                    ('importante', 20, 30, 1),
                    ('confort', 35, 45, 2),
                    ('prescindible', 50, 60, 3)
                ) AS p (codigo, apagado, reactivacion, orden)
                ORDER BY abs(d.umbral_apagado_soc - p.apagado) + abs(d.umbral_reactivacion_soc - p.reactivacion),
                         p.orden
                LIMIT 1
            )
        END
        """
    )
    op.alter_column('dispositivos', 'categoria', nullable=False)

    # 3. Prioridad derivada
    op.execute(
        """
        UPDATE dispositivos
        SET prioridad = CASE categoria
            WHEN 'esencial' THEN 40
            WHEN 'importante' THEN 30
            WHEN 'confort' THEN 20
            WHEN 'prescindible' THEN 10
        END
        """
    )

    # 4. Ubicación y activo
    op.add_column('dispositivos', sa.Column('ubicacion', sa.String(length=100), nullable=True))
    op.add_column('dispositivos', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))

    # 5. CHECK de umbrales. El de la banda conserva el nombre del que reemplaza.
    op.drop_constraint('dispositivos_umbrales_soc_check', 'dispositivos', type_='check')
    for nombre, condicion in CHECK_NUEVOS:
        op.create_check_constraint(nombre, 'dispositivos', condicion)


def downgrade() -> None:
    """Downgrade schema."""
    # 5. CHECK de umbrales
    for nombre, _ in CHECK_NUEVOS:
        op.drop_constraint(nombre, 'dispositivos', type_='check')
    op.create_check_constraint(
        'dispositivos_umbrales_soc_check',
        'dispositivos',
        'umbral_reactivacion_soc > umbral_apagado_soc',
    )

    # 4. Ubicación y activo
    op.drop_column('dispositivos', 'activo')
    op.drop_column('dispositivos', 'ubicacion')

    # 3. La prioridad anterior no se puede recuperar: se deja la derivada.

    # 2. Categoría
    op.drop_constraint('dispositivos_categoria_fkey', 'dispositivos', type_='foreignkey')
    op.drop_column('dispositivos', 'categoria')

    # 1. Catálogo (pierde RLS al eliminarlo)
    op.drop_table('categoria_dispositivo')
