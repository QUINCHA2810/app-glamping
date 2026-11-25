"""
Prueba rápida de conexión: verifica que VS Code puede llegar a la base
de datos de Supabase y que las 12 tablas están visibles.

Uso: python test_connection.py
"""

from sqlalchemy import text
from database import engine

TABLAS_ESPERADAS = {
    "usuarios", "domos", "administrador_domo", "dispositivos",
    "lecturas_fotovoltaicas", "lecturas_consumo", "lecturas_bateria",
    "lecturas_sensores", "alertas", "historial_acciones",
    "estado_comunicacion", "configuracion_sistema",
}

def main():
    try:
        with engine.connect() as conn:
            resultado = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tablas_encontradas = {fila[0] for fila in resultado}

        print(f"✅ Conexión exitosa. {len(tablas_encontradas)} tablas encontradas:\n")
        for tabla in sorted(tablas_encontradas):
            marca = "✓" if tabla in TABLAS_ESPERADAS else "?"
            print(f"  [{marca}] {tabla}")

        faltantes = TABLAS_ESPERADAS - tablas_encontradas
        if faltantes:
            print(f"\n⚠️  Faltan tablas esperadas: {faltantes}")
        else:
            print("\n✅ Las 12 tablas del esquema están presentes.")

    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        print("\nRevisa: 1) que .env tenga la contraseña correcta, "
              "2) que no haya espacios extra en DATABASE_URL.")


if __name__ == "__main__":
    main()
