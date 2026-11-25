"""integridad de datos, fase 1

En este orden (todo en una sola transacción: si algo falla, no queda nada):

1. Limpieza: lecturas_fotovoltaicas.estado_generacion 'activa' -> 'generando'.
2. Tablas catálogo con el TEXTO como clave primaria, ya sembradas.
3. FK desde las columnas de texto existentes: el backend sigue escribiendo
   los mismos valores, sin JOIN.
4. Se quitan los 3 CHECK de valores permitidos: la FK ya los cubre, y dejarlos
   obligaría a tocar dos sitios para agregar un valor.
5. CHECK de rango de SoC y de umbrales; UNIQUE(domo_id, id_fisico) (varios
   NULL siguen permitidos: son los dispositivos simulados) y UNIQUE(id, domo_id).
6. FK compuestas (dispositivo_id, domo_id) -> dispositivos(id, domo_id) en
   lugar de las simples. Llevan ON DELETE SET NULL (dispositivo_id), con la
   columna explícita: sin ella, borrar un dispositivo intentaría poner también
   domo_id en NULL, que es NOT NULL, y el borrado fallaría (requiere Postgres
   15 o superior; Supabase corre 17).
7. Tabla sensores y columna opcional sensor_id en las 4 tablas de lecturas.
8. RLS sin políticas en todas las tablas de public. El backend, el listener y
   las migraciones conectan como `postgres`, dueño de las tablas y con
   BYPASSRLS, así que no les afecta; a la API de datos de Supabase (anon y
   authenticated), que nada en este proyecto usa, le deja de devolver filas.

Por qué rol_actor separado de rol_usuario: `sistema` no es una persona y el
cliente nunca tiene cuenta (docs/Requisitos/Usuariosypermisos.md), así que esos
valores no deben ser válidos en usuarios.rol.

Migración escrita a mano (sin --autogenerate) a propósito: models.py no declara
los CHECK ni los ON DELETE reales, así que autogenerate arrastraría diferencias
no relacionadas (ver backend/README.md, sección "Migraciones").

downgrade() deja el esquema exactamente como estaba, pero PIERDE lo que se haya
guardado en sensores y en sensor_id, y vuelve a poner 'activa' en las 3 filas
originales (ids 1, 2 y 3).

Revision ID: b7d2c4e8a1f3
Revises: e1857d5e5e9d
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2c4e8a1f3'
down_revision: Union[str, Sequence[str], None] = 'e1857d5e5e9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Filas que tenían 'activa' antes de esta migración (verificado contra Supabase).
IDS_ESTADO_ACTIVA = (1, 2, 3)

# catálogo -> [(codigo, descripcion)]
CATALOGOS = {
    'rol_usuario': [
        ('dueño', 'Propietario de uno o más domos'),
        ('administrador', 'Administra los domos que un dueño le asigna'),
    ],
    'rol_actor': [
        ('dueño', 'Acción de un usuario dueño'),
        ('administrador', 'Acción de un usuario administrador'),
        ('cliente', 'Acción del huésped sobre un dispositivo físico; no tiene cuenta'),
        ('sistema', 'Acción automática del algoritmo de supervivencia (R-13)'),
    ],
    'tipo_dispositivo': [
        ('bombillo', 'Bombillo controlable'),
        ('toma', 'Tomacorriente controlable'),
        ('otro', 'Otro tipo de dispositivo'),
    ],
    'tipo_accion': [
        ('encender', 'Encendido de un dispositivo'),
        ('apagar', 'Apagado de un dispositivo'),
    ],
    'tipo_alerta': [
        ('bateria_baja', 'Estado de carga de la batería bajo'),
        ('consumo_elevado', 'Consumo por encima de lo esperado'),
        ('generacion_nula', 'Sin generación fotovoltaica'),
        ('dispositivo_desconectado', 'Un dispositivo perdió la comunicación'),
        ('sobrecarga', 'Sobrecarga del sistema'),
    ],
    'nivel_prioridad': [
        ('informativo', 'Solo informa'),
        ('advertencia', 'Requiere atención'),
        ('critico', 'Requiere atención inmediata'),
    ],
    'estado_alerta': [
        ('activa', 'Sin atender'),
        ('atendida', 'Vista o resuelta por el sistema o un usuario'),
        ('cerrada', 'Cerrada por un usuario'),
    ],
    'estado_bateria': [
        ('cargando', 'La batería se está cargando'),
        ('descargando', 'La batería se está descargando'),
        ('cargada', 'SoC al 100 % con excedente de generación'),
        ('en_reposo', 'Sin flujo neto'),
    ],
    'estado_generacion': [
        ('generando', 'Los paneles están generando'),
        ('sin_generacion', 'Los paneles no están generando'),
    ],
    'tipo_lectura': [
        ('bateria', 'Medición de la batería'),
        ('fotovoltaica', 'Medición de la generación fotovoltaica'),
        ('consumo', 'Medición del consumo'),
        ('temperatura', 'Medición de temperatura'),
    ],
}

# (nombre de la FK, tabla, columna, catálogo)
FK_TEXTO = [
    ('usuarios_rol_fkey', 'usuarios', 'rol', 'rol_usuario'),
    ('dispositivos_tipo_fkey', 'dispositivos', 'tipo', 'tipo_dispositivo'),
    ('historial_acciones_rol_fkey', 'historial_acciones', 'rol', 'rol_actor'),
    ('historial_acciones_accion_realizada_fkey', 'historial_acciones', 'accion_realizada', 'tipo_accion'),
    ('alertas_tipo_alerta_fkey', 'alertas', 'tipo_alerta', 'tipo_alerta'),
    ('alertas_nivel_prioridad_fkey', 'alertas', 'nivel_prioridad', 'nivel_prioridad'),
    ('alertas_estado_alerta_fkey', 'alertas', 'estado_alerta', 'estado_alerta'),
    ('lecturas_bateria_estado_bateria_fkey', 'lecturas_bateria', 'estado_bateria', 'estado_bateria'),
    ('lecturas_fotovoltaicas_estado_generacion_fkey', 'lecturas_fotovoltaicas', 'estado_generacion', 'estado_generacion'),
]

# (nombre, tabla, condición) — definición original, para el downgrade.
CHECK_ANTIGUOS = [
    ('usuarios_rol_check', 'usuarios', "rol = ANY (ARRAY['dueño'::text, 'administrador'::text])"),
    ('dispositivos_tipo_check', 'dispositivos', "tipo = ANY (ARRAY['bombillo'::text, 'toma'::text, 'otro'::text])"),
    (
        'historial_acciones_rol_check',
        'historial_acciones',
        "rol = ANY (ARRAY['dueño'::text, 'administrador'::text, 'cliente'::text, 'sistema'::text])",
    ),
]

# (nombre de la FK compuesta, nombre de la FK simple que reemplaza, tabla)
FK_DISPOSITIVO = [
    ('historial_acciones_dispositivo_domo_fkey', 'historial_acciones_dispositivo_id_fkey', 'historial_acciones'),
    ('lecturas_consumo_dispositivo_domo_fkey', 'lecturas_consumo_dispositivo_id_fkey', 'lecturas_consumo'),
]

TABLAS_LECTURAS = ['lecturas_bateria', 'lecturas_fotovoltaicas', 'lecturas_consumo', 'lecturas_sensores']

TABLAS_ANTERIORES = [
    'administrador_domo',
    'alembic_version',
    'alertas',
    'configuracion_sistema',
    'dispositivos',
    'domos',
    'estado_comunicacion',
    'historial_acciones',
    'lecturas_bateria',
    'lecturas_consumo',
    'lecturas_fotovoltaicas',
    'lecturas_sensores',
    'usuarios',
]


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Limpieza
    op.execute("UPDATE lecturas_fotovoltaicas SET estado_generacion = 'generando' WHERE estado_generacion = 'activa'")

    # 2. Catálogos y siembra
    for catalogo, valores in CATALOGOS.items():
        tabla = op.create_table(
            catalogo,
            sa.Column('codigo', sa.Text(), primary_key=True),
            sa.Column('descripcion', sa.Text(), nullable=True),
        )
        op.bulk_insert(tabla, [{'codigo': codigo, 'descripcion': descripcion} for codigo, descripcion in valores])

    # 3. FK de texto
    for nombre, tabla, columna, catalogo in FK_TEXTO:
        op.create_foreign_key(nombre, tabla, catalogo, [columna], ['codigo'])

    # 4. Quitar los 3 CHECK
    for nombre, tabla, _ in CHECK_ANTIGUOS:
        op.drop_constraint(nombre, tabla, type_='check')

    # 5. CHECK nuevos y UNIQUE. Un NULL hace que el CHECK no se aplique, así
    # que el de umbrales solo actúa cuando ambos tienen valor. Postgres trata
    # NaN como mayor que cualquier número: el de SoC también lo rechaza.
    op.create_check_constraint(
        'lecturas_bateria_estado_carga_soc_check',
        'lecturas_bateria',
        'estado_carga_soc >= 0 AND estado_carga_soc <= 100',
    )
    op.create_check_constraint(
        'dispositivos_umbrales_soc_check',
        'dispositivos',
        'umbral_reactivacion_soc > umbral_apagado_soc',
    )
    op.create_unique_constraint('dispositivos_domo_id_id_fisico_key', 'dispositivos', ['domo_id', 'id_fisico'])
    op.create_unique_constraint('dispositivos_id_domo_id_key', 'dispositivos', ['id', 'domo_id'])

    # 6. FK compuestas en lugar de las simples
    for compuesta, simple, tabla in FK_DISPOSITIVO:
        op.drop_constraint(simple, tabla, type_='foreignkey')
        op.create_foreign_key(
            compuesta,
            tabla,
            'dispositivos',
            ['dispositivo_id', 'domo_id'],
            ['id', 'domo_id'],
            ondelete='SET NULL (dispositivo_id)',
        )

    # 7. Sensores y sensor_id
    op.create_table(
        'sensores',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('domo_id', sa.BigInteger(), sa.ForeignKey('domos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tipo_lectura', sa.Text(), sa.ForeignKey('tipo_lectura.codigo'), nullable=False),
        sa.Column('nombre', sa.Text(), nullable=False),
        sa.Column('unidad_medida', sa.Text(), nullable=True),
        sa.Column('id_fisico', sa.Text(), nullable=True),
    )
    for tabla in TABLAS_LECTURAS:
        op.add_column(tabla, sa.Column('sensor_id', sa.BigInteger(), nullable=True))
        op.create_foreign_key(f'{tabla}_sensor_id_fkey', tabla, 'sensores', ['sensor_id'], ['id'], ondelete='SET NULL')

    # 8. RLS
    for tabla in [*TABLAS_ANTERIORES, *CATALOGOS, 'sensores']:
        op.execute(f'ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    # 8. RLS (las tablas nuevas lo pierden al eliminarlas)
    for tabla in TABLAS_ANTERIORES:
        op.execute(f'ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY')

    # 7. Sensores y sensor_id
    for tabla in TABLAS_LECTURAS:
        op.drop_column(tabla, 'sensor_id')
    op.drop_table('sensores')

    # 6. FK simples en lugar de las compuestas
    for compuesta, simple, tabla in FK_DISPOSITIVO:
        op.drop_constraint(compuesta, tabla, type_='foreignkey')
        op.create_foreign_key(simple, tabla, 'dispositivos', ['dispositivo_id'], ['id'], ondelete='SET NULL')

    # 5. CHECK nuevos y UNIQUE
    op.drop_constraint('dispositivos_id_domo_id_key', 'dispositivos', type_='unique')
    op.drop_constraint('dispositivos_domo_id_id_fisico_key', 'dispositivos', type_='unique')
    op.drop_constraint('dispositivos_umbrales_soc_check', 'dispositivos', type_='check')
    op.drop_constraint('lecturas_bateria_estado_carga_soc_check', 'lecturas_bateria', type_='check')

    # 4. Volver a poner los 3 CHECK
    for nombre, tabla, condicion in CHECK_ANTIGUOS:
        op.create_check_constraint(nombre, tabla, condicion)

    # 3. FK de texto
    for nombre, tabla, _, _ in FK_TEXTO:
        op.drop_constraint(nombre, tabla, type_='foreignkey')

    # 2. Catálogos
    for catalogo in CATALOGOS:
        op.drop_table(catalogo)

    # 1. Limpieza
    ids = ', '.join(str(id_lectura) for id_lectura in IDS_ESTADO_ACTIVA)
    op.execute(f"UPDATE lecturas_fotovoltaicas SET estado_generacion = 'activa' WHERE id IN ({ids})")
