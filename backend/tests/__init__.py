"""
Protección de todas las pruebas: Python ejecuta este archivo antes que cualquier
tests.<módulo>, así que corre antes de importar database, models o main.

1. DATABASE_URL_V2 (la que usa database.py en esta rama) y DATABASE_URL apuntan a SQLite
   en memoria antes de que database.py lea backend/.env (load_dotenv no pisa una variable
   ya definida).
2. create_engine se reemplaza por una versión que falla de inmediato si la URL no es
   SQLite ni de esta máquina: ninguna prueba puede abrir un motor contra Supabase,
   aunque un archivo importe models antes que tests.base o lea otra variable.

Las pruebas de Postgres (TEST_POSTGRES_URL) pasan porque apuntan a localhost.
"""

import os

os.environ["DATABASE_URL_V2"] = "sqlite://"
os.environ["DATABASE_URL"] = "sqlite://"

import sqlalchemy  # noqa: E402
import sqlalchemy.engine  # noqa: E402
import sqlalchemy.engine.create as _create  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

HOSTS_LOCALES = ("localhost", "127.0.0.1", "::1")
_create_engine_original = _create.create_engine


def url_permitida(url) -> bool:
    url = make_url(url)
    if url.get_backend_name() == "sqlite":
        return True
    return url.host in HOSTS_LOCALES


def _create_engine_vigilado(url, *args, **kwargs):
    if not url_permitida(url):
        # Sin la URL en el mensaje: podría traer la contraseña.
        raise RuntimeError(
            "Prueba bloqueada: se intentó crear un motor de base de datos que no es SQLite ni localhost. "
            "Importa tests.base antes que database, models o main."
        )
    return _create_engine_original(url, *args, **kwargs)


if not getattr(_create.create_engine, "vigilado", False):
    _create_engine_vigilado.vigilado = True
    # engine_from_config (alembic) llama al create_engine de este módulo.
    sqlalchemy.create_engine = sqlalchemy.engine.create_engine = _create.create_engine = _create_engine_vigilado
