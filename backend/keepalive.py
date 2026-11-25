"""
Ping periodico para evitar que Supabase pause los proyectos por
inactividad (el plan gratuito pausa un proyecto tras varios dias sin
actividad real de base de datos). Este script lo ejecuta GitHub Actions
segun una programacion (ver .github/workflows/supabase-keepalive.yml) --
no hace falta correrlo a mano ni dejar tu computador prendido.

Hace una consulta trivial (SELECT now()) en cada base configurada:
- DATABASE_URL: el proyecto v1 (se conserva para poder volver a v1).
- DATABASE_URL_V2: el proyecto v2 (ronda DB-3), el que usa la app desde el cambio.
Las dos son opcionales, pero tiene que haber al menos una. Si alguna configurada
falla, termina con error (GitHub avisa por correo). Nunca imprime la URL ni el texto
del error de conexion, que puede incluir el host o el usuario.
"""

import os
import sys

from sqlalchemy import create_engine, text

BASES = (("v1", "DATABASE_URL"), ("v2", "DATABASE_URL_V2"))


def ping(url: str) -> str:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT now()")).scalar())
    finally:
        engine.dispose()


def main() -> int:
    configuradas = [(nombre, variable, os.getenv(variable)) for nombre, variable in BASES if os.getenv(variable)]
    if not configuradas:
        print("ERROR: no esta definida ni DATABASE_URL ni DATABASE_URL_V2.")
        return 1
    fallas = 0
    for nombre, variable, url in configuradas:
        try:
            print(f"OK -- Supabase {nombre} ({variable}) respondio: {ping(url)}")
        except Exception as error:  # noqa: BLE001 -- cualquier falla cuenta, sin mostrar su texto
            fallas += 1
            print(f"ERROR -- Supabase {nombre} ({variable}) no respondio ({type(error).__name__}).")
    for nombre, variable in BASES:
        if not os.getenv(variable):
            print(f"Aviso -- {variable} no esta definida: no se hizo ping a {nombre}.")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
