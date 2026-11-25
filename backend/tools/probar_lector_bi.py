"""Prueba de mínimo privilegio del rol lector_bi (usado por Power BI).

Se conecta por el Session pooler como lector_bi y comprueba que:
  1. puede leer las vistas del esquema bi,
  2. NO puede leer las tablas de public (usuarios),
  3. NO puede escribir (transacción de solo lectura).
La contraseña se pide con getpass: no se muestra, no se imprime y no se guarda.

Sirve para v1 y para v2: cada proyecto de Supabase tiene SU lector_bi, con
su propia contraseña. Por eso pide la referencia del proyecto (la que va después de
"postgres." en el usuario del Session pooler; también está en la URL del proyecto).
No se guarda en el código: se escribe al correrlo.

    python tools/probar_lector_bi.py
"""
import getpass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

def _driver() -> str:
    try:
        import psycopg  # noqa: F401
        return "postgresql+psycopg"
    except ImportError:
        return "postgresql+psycopg2"


def _debe_fallar(con, sql: str, texto_esperado: str, descripcion: str) -> bool:
    try:
        con.execute(text(sql))
    except DBAPIError as e:
        con.rollback()
        mensaje = str(e.orig).splitlines()[0]
        if texto_esperado in mensaje:
            print(f"OK  {descripcion}: bloqueado ({mensaje})")
            return True
        print(f"??  {descripcion}: falló, pero por otra razón ({mensaje})")
        return False
    con.rollback()
    print(f"MAL {descripcion}: ¡se permitió!")
    return False


def main() -> None:
    ref = input("Referencia del proyecto de Supabase: ").strip()
    if not ref:
        raise SystemExit("Falta la referencia del proyecto.")
    host = input("Host del Session pooler (sin puerto): ").strip()
    clave = getpass.getpass("Contraseña de lector_bi (no se muestra): ")
    url = URL.create(
        _driver(),
        username=f"lector_bi.{ref}",
        password=clave,
        host=host,
        port=5432,
        database="postgres",
        query={"sslmode": "require"},
    )
    motor = create_engine(url)
    resultados = []
    try:
        with motor.connect() as con:
            usuario = con.execute(text("SELECT current_user")).scalar()
            solo_lectura = con.execute(text("SHOW transaction_read_only")).scalar()
            print(f"Conectado como: {usuario} | solo lectura: {solo_lectura}")
            resultados.append(usuario == "lector_bi" and solo_lectura == "on")

            n = con.execute(text("SELECT count(*) FROM bi.v_estado_actual")).scalar()
            print(f"OK  lectura de bi.v_estado_actual: {n} fila(s)")
            con.rollback()
            resultados.append(True)

            resultados.append(_debe_fallar(
                con, "SELECT count(*) FROM public.usuarios",
                "permission denied", "lectura de public.usuarios"))
            resultados.append(_debe_fallar(
                con, "CREATE TABLE bi.prueba_escritura (x int)",
                "read-only transaction", "escritura en la base"))
    finally:
        motor.dispose()

    print("\nRESULTADO:", "TODO CORRECTO" if all(resultados) else "REVISAR")


if __name__ == "__main__":
    main()