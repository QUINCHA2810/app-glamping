"""
Conexión SQLAlchemy a la base de datos del sistema de gestión energética del domo
experimental.

En esta rama (modelo v2, docs/modelo_v2/MAPEO.md) la base es la de DATABASE_URL_V2:
el backend, el listener y las herramientas entran por aquí. La versión v1 (etiqueta
`v1-modelo-actual`) usa DATABASE_URL, así que volver atrás es cambiar de rama.

Las únicas excepciones, que leen DATABASE_URL a propósito y por su cuenta, son
tools/respaldo_bd.py con `--origen v1` (el respaldo de la base v1 del que sale la copia
definitiva) y alembic_v2/env.py, que solo la compara para negarse si es la misma base.

Ningún mensaje imprime la URL: lleva la contraseña.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

VARIABLE_URL = "DATABASE_URL_V2"
DATABASE_URL = os.getenv(VARIABLE_URL)

if not DATABASE_URL:
    raise RuntimeError(
        f"{VARIABLE_URL} no está definida. Esta rama usa la base del modelo v2: agrega "
        f"{VARIABLE_URL} a backend/.env con su cadena de conexión (ver env.example). "
        "Para la base v1, cambia a la etiqueta v1-modelo-actual."
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependencia de FastAPI: entrega una sesión y la cierra al terminar."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
