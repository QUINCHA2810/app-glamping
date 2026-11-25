"""
Respaldo de la base de datos — exporta TODAS las tablas del esquema public a
archivos JSON.

--origen elige la base (por defecto v1):
- v1: la de DATABASE_URL, la base del modelo v1. De este respaldo sale la copia a v2
  (tools/copiar_a_v2.py), así que se puede seguir sacando desde esta rama.
- v2: la de DATABASE_URL_V2, la misma del backend en esta rama (database.py).
El manifiesto dice de qué origen es, y copiar_a_v2.py se niega a copiar uno de v2.

Crea una carpeta respaldo_AAAAMMDD_HHMM (fuera de git, ver .gitignore) con:

    <tabla>.json        lista de filas, una por objeto
    _manifiesto.json    fecha, versión de Alembic, filas y columnas por tabla,
                        y las restricciones reales (los CHECK y los ON DELETE
                        no están en models.py, solo en la base de datos)

_manifiesto.json se escribe de último: una carpeta sin él es un respaldo
incompleto y no se debe usar.

Solo lee: todo corre en una única transacción READ ONLY y REPEATABLE READ,
así que las tablas quedan coherentes entre sí aunque el listener MQTT siga
insertando lecturas mientras tanto.

Respalda datos y restricciones, no es un volcado restaurable por sí solo: los
índices y las secuencias no se exportan.

OJO: usuarios.json contiene contrasena_hash y token_autenticacion. Trata la
carpeta como un secreto, y ten en cuenta que dentro de una carpeta sincronizada
(OneDrive) también se sube a la nube; usa --destino para guardarla en otro lado.

Uso (desde backend/, con el .venv activado):

    python tools/respaldo_bd.py                       # base v1 (DATABASE_URL)
    python tools/respaldo_bd.py --destino D:/respaldos
    python tools/respaldo_bd.py --origen v2           # base v2 (DATABASE_URL_V2)
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import text

# Al ejecutar tools/respaldo_bd.py, sys.path[0] es tools/, no backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

ORIGENES = {"v1": "DATABASE_URL", "v2": "DATABASE_URL_V2"}


def motor_de(origen: str):
    """Motor de la base del origen pedido. La URL nunca se imprime."""
    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv(BACKEND_DIR / ".env")
    variable = ORIGENES[origen]
    url = os.getenv(variable)
    if not url:
        sys.exit(f"{variable} no está definida (backend/.env o el entorno): no se respaldó nada.")
    return create_engine(url)


def _a_json(valor: object) -> object:
    """Convierte un valor de Postgres a algo que JSON represente sin perderlo.
    NaN/Infinity van como texto: json.dump los escribiría como tokens que no
    son JSON válido."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    if valor is None or isinstance(valor, (bool, int, float, str)):
        return valor
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    if isinstance(valor, (bytes, memoryview)):
        return bytes(valor).hex()
    return str(valor)  # Decimal, UUID, etc.


def _tablas_publicas(con) -> list[str]:
    return list(con.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )).scalars())


def _columnas(con, tabla: str) -> dict[str, str]:
    filas = con.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :tabla ORDER BY ordinal_position"
    ), {"tabla": tabla}).all()
    return {columna: tipo for columna, tipo in filas}


def _restricciones(con) -> list[dict]:
    filas = con.execute(text(
        "SELECT conrelid::regclass::text AS tabla, conname AS nombre, "
        "pg_get_constraintdef(oid) AS definicion "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
        "ORDER BY 1, 2"
    )).mappings().all()
    return [dict(fila) for fila in filas]


def _exportar_tabla(con, tabla: str, carpeta: Path) -> int:
    """Escribe <tabla>.json y lo vuelve a leer para confirmar que quedó
    completo y es JSON válido. Devuelve el número de filas."""
    nombre_sql = con.dialect.identifier_preparer.quote(tabla)
    filas = [
        {columna: _a_json(valor) for columna, valor in fila.items()}
        for fila in con.execute(text(f"SELECT * FROM {nombre_sql} ORDER BY 1")).mappings()
    ]

    archivo = carpeta / f"{tabla}.json"
    with archivo.open("w", encoding="utf-8") as salida:
        json.dump(filas, salida, ensure_ascii=False, indent=1, allow_nan=False)

    with archivo.open(encoding="utf-8") as entrada:
        releidas = len(json.load(entrada))
    if releidas != len(filas):
        raise RuntimeError(f"{archivo.name}: se escribieron {len(filas)} filas pero se leyeron {releidas}.")
    return len(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta todas las tablas públicas a JSON.")
    parser.add_argument("--destino", type=Path, default=BACKEND_DIR,
                        help="carpeta donde crear respaldo_AAAAMMDD_HHMM (por defecto backend/)")
    parser.add_argument("--origen", choices=sorted(ORIGENES), default="v1",
                        help="v1 (DATABASE_URL, por defecto) o v2 (DATABASE_URL_V2)")
    args = parser.parse_args()
    engine = motor_de(args.origen)

    ahora = datetime.now().astimezone()
    # exist_ok=False: un respaldo nunca sobrescribe a otro.
    carpeta = args.destino / f"respaldo_{ahora:%Y%m%d_%H%M}"
    carpeta.mkdir(parents=True, exist_ok=False)

    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as con:
        con.execute(text("SET TRANSACTION READ ONLY"))

        tablas = {}
        for tabla in _tablas_publicas(con):
            filas = _exportar_tabla(con, tabla, carpeta)
            tablas[tabla] = {"filas": filas, "columnas": _columnas(con, tabla)}
            print(f"  {tabla}: {filas} filas")

        manifiesto = {
            "origen": args.origen,
            "fecha": ahora.isoformat(),
            "version_alembic": con.execute(text("SELECT version_num FROM alembic_version")).scalar(),
            "tablas": tablas,
            "restricciones": _restricciones(con),
        }
        con.rollback()

    with (carpeta / "_manifiesto.json").open("w", encoding="utf-8") as salida:
        json.dump(manifiesto, salida, ensure_ascii=False, indent=1)

    engine.dispose()
    print(f"Respaldo completo de la base {args.origen}: {len(tablas)} tablas en {carpeta}")


if __name__ == "__main__":
    main()
