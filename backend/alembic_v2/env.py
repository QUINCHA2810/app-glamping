"""
Entorno de Alembic del modelo v2 (docs/modelo_v2/MAPEO.md).

Es una línea de migraciones aparte de backend/alembic/: la base v2 empieza vacía y
su primera revisión crea las 7 tablas. Se corre con

    alembic -c alembic_v2.ini upgrade head

La URL sale SOLO de DATABASE_URL_V2 (backend/.env o el entorno). Nunca se usa
DATABASE_URL, que es la base v1, y si las dos apuntan a la misma base (mismo
servidor, puerto, base y usuario) se niega a correr. La revisión base además se
niega si encuentra tablas del modelo v1.

Ningún mensaje de este archivo imprime la URL: lleva la contraseña.

Sin target_metadata: las migraciones de esta línea se escriben a mano, igual que las
de v1 (no usar --autogenerate).
"""

import os
import sys
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

VARIABLE = "DATABASE_URL_V2"


def _identidad(url: str) -> tuple:
    u = make_url(url)
    return (u.get_backend_name(), u.host, u.port, u.database, u.username)


def _url_v2() -> str:
    # load_dotenv no pisa variables ya definidas en el entorno.
    load_dotenv()
    url = os.getenv(VARIABLE)
    if not url:
        raise RuntimeError(
            f"{VARIABLE} no está definida. Ponla en backend/.env (o en el entorno) con la "
            "cadena de conexión de la base v2. Esta línea de migraciones nunca usa DATABASE_URL."
        )
    v1 = os.getenv("DATABASE_URL")
    if v1 and _identidad(v1) == _identidad(url):
        raise RuntimeError(
            f"{VARIABLE} apunta a la misma base que DATABASE_URL (la v1). No se aplica nada."
        )
    return url


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (alembic ... --sql)."""
    context.configure(
        url=_url_v2(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    seccion = config.get_section(config.config_ini_section, {})
    seccion["sqlalchemy.url"] = _url_v2()
    connectable = engine_from_config(seccion, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=False)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
