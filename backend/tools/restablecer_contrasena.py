"""
Herramienta de emergencia: pone una contraseña nueva a una cuenta, por ejemplo cuando
el dueño olvidó la suya y nadie puede restablecérsela desde la app.

Corre solo en el PC que tiene backend/.env, y es interactiva:

    python tools/restablecer_contrasena.py

Pide el correo, muestra el nombre y el rol de la cuenta para confirmar y pide la
contraseña nueva dos veces, sin mostrarla. Aplica la misma validación que la API y el
mismo hash, cierra todas las sesiones abiertas de la cuenta (sube version_sesion) y
deja debe_cambiar_contrasena en false: quien la elige aquí es el propio usuario.

Nunca imprime la DATABASE_URL, hashes ni la contraseña. Ver backend/README.md.
"""

import getpass
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

NOMBRE_ROL = {"dueño": "Dueño", "administrador": "Administrador"}


def restablecer(db, preguntar=input, pedir_clave=getpass.getpass, decir=print) -> bool:
    """True si cambió la contraseña. Las entradas y salidas se reciben para probarla."""
    from pydantic import TypeAdapter, ValidationError

    from errores_validacion import traducir_errores
    from models import Usuario
    from schemas import ContrasenaNueva
    from security import cambiar_contrasena

    correo = preguntar("Correo de la cuenta: ").strip()
    usuario = db.query(Usuario).filter(Usuario.correo == correo).first()
    if usuario is None:
        decir("No hay ninguna cuenta con ese correo.")
        return False

    estado = "" if usuario.estado_cuenta == "activo" else " (cuenta desactivada: seguirá sin poder entrar)"
    decir(f"Cuenta: {usuario.nombre} {usuario.apellido or ''}".rstrip() + f" — {NOMBRE_ROL.get(usuario.rol, usuario.rol)}{estado}")
    if preguntar("¿Es esta la cuenta? Escribe s para continuar: ").strip().lower() not in ("s", "si", "sí"):
        decir("Cancelado. No se cambió nada.")
        return False

    validar = TypeAdapter(ContrasenaNueva)
    for _ in range(3):
        nueva = pedir_clave("Contraseña nueva: ")
        try:
            validar.validate_python(nueva)
        except ValidationError as exc:
            decir(traducir_errores(exc.errors())[0]["msg"])
            continue
        if pedir_clave("Repítela: ") != nueva:
            decir("No coinciden.")
            continue
        cambiar_contrasena(usuario, nueva)
        usuario.debe_cambiar_contrasena = False
        db.commit()
        decir("Contraseña cambiada. Se cerraron todas las sesiones abiertas de la cuenta.")
        return True

    decir("Demasiados intentos. No se cambió nada.")
    return False


def main() -> None:
    try:
        from database import SessionLocal

        with SessionLocal() as db:
            cambiada = restablecer(db)
    except KeyboardInterrupt:
        sys.exit("\nCancelado. No se cambió nada.")
    except Exception as exc:
        # Solo el tipo: el texto de un error de conexión puede traer la URL con la clave.
        sys.exit(f"No se pudo completar ({type(exc).__name__}). Revisa la conexión y que las migraciones estén aplicadas.")
    sys.exit(0 if cambiada else 1)


if __name__ == "__main__":
    main()
