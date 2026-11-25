"""
Utilidades de seguridad para el requisito R-1 (Autenticación):

- Hash y verificación de contraseñas (passlib + bcrypt).
- Creación y verificación de JWT.
- `get_current_user`: dependencia que cualquier otro endpoint puede usar
  para exigir un token válido antes de dejar pasar la petición.
- `require_dueno`: igual que la anterior, pero además exige que la cuenta sea
  dueña de al menos un domo (para /auth/registro, /administradores y crear domos).
  Las acciones del dueño SOBRE UN DOMO miran además el rol en ese domo
  (routers/domos.py, domo_del_dueno): un usuario puede ser dueño de un domo y
  administrador de otro.

No expone rutas propias; routers/auth.py es el que arma los endpoints
usando estas funciones.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario
from publicacion import docs_habilitadas

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY no está definida. Agrégala a backend/.env "
        "(ver env.example) — debe ser un valor largo y aleatorio."
    )

LONGITUD_MINIMA_SECRETO = 32

logger = logging.getLogger("uvicorn.error.security")


def validar_secreto(secreto: str, publico: bool) -> None:
    """Un secreto de menos de LONGITUD_MINIMA_SECRETO caracteres se puede adivinar
    por fuerza bruta con un solo token. Lo registra como ERROR; en modo público
    (API_DOCS=false, docs/publicacion_web.md) además impide arrancar."""
    if len(secreto) >= LONGITUD_MINIMA_SECRETO:
        return
    mensaje = (
        f"JWT_SECRET_KEY tiene {len(secreto)} caracteres; se necesitan al menos "
        f"{LONGITUD_MINIMA_SECRETO}. Genera uno con: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
    logger.error(mensaje)
    if publico:
        raise RuntimeError(mensaje + " (modo público: API_DOCS=false)")


validar_secreto(SECRET_KEY, publico=not docs_habilitadas())

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTPBearer (no OAuth2PasswordBearer): nuestro login recibe JSON
# (correo/contrasena), no el formulario username/password que espera el
# flujo OAuth2 estándar. Con HTTPBearer, el botón "Authorize" de Swagger
# UI (/docs) simplemente pide pegar el JWT ya emitido por /auth/login,
# que es como funciona esta API en la práctica.
# auto_error=False: si no viene token, devolvemos nuestro propio 401 más
# abajo en vez de dejar que FastAPI lance uno genérico.
http_bearer_scheme = HTTPBearer(auto_error=False)

credenciales_invalidas = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token inválido o expirado.",
    headers={"WWW-Authenticate": "Bearer"},
)


def hash_password(contrasena: str) -> str:
    """Genera el hash bcrypt que se guarda en usuarios.contrasena_hash."""
    return pwd_context.hash(contrasena)


def verify_password(contrasena_plana: str, contrasena_hash: str) -> bool:
    """Compara una contraseña en texto plano contra su hash bcrypt."""
    return pwd_context.verify(contrasena_plana, contrasena_hash)


def crear_token_acceso(usuario: Usuario) -> str:
    """Genera el JWT firmado que identifica al usuario autenticado.

    El payload incluye el id del usuario (`sub`), la versión de sesión (`ver`) y
    `rol`, que en v2 se calcula de las asignaciones ('dueño' si es dueña de algún
    domo): solo informa. Ningún permiso se decide con él; get_current_user vuelve a
    leer la cuenta en cada petición y cada endpoint mira el rol en su domo.
    """
    expira = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(usuario.id), "rol": usuario.rol, "exp": expira, "ver": usuario.version_sesion}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def cambiar_contrasena(usuario: Usuario, contrasena_nueva: str) -> None:
    """Guarda la contraseña nueva y cierra todas las sesiones abiertas de la cuenta.
    Solo modifica la fila: el commit es de quien llama."""
    usuario.contrasena_hash = hash_password(contrasena_nueva)
    usuario.version_sesion += 1


# Lo único que puede hacer una cuenta con contraseña temporal.
PERMITIDO_CON_CONTRASENA_TEMPORAL = {("GET", "/users/me"), ("POST", "/users/me/contrasena")}


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    """Dependencia para proteger endpoints: exige un JWT válido en el
    header `Authorization: Bearer <token>` y devuelve el usuario dueño de
    ese token.

    Uso en cualquier endpoint nuevo:

        from security import get_current_user

        @app.get("/lo-que-sea")
        def endpoint(usuario: Usuario = Depends(get_current_user)):
            ...
    """
    if credentials is None:
        raise credenciales_invalidas

    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except PyJWTError:
        raise credenciales_invalidas

    usuario_id = payload.get("sub")
    if usuario_id is None:
        raise credenciales_invalidas

    usuario = db.get(Usuario, int(usuario_id))
    if usuario is None:
        raise credenciales_invalidas

    # Un token de antes de cerrar las sesiones trae otra versión. Los emitidos antes de
    # que existiera este dato no la traen: valen como versión 0.
    if payload.get("ver", 0) != usuario.version_sesion:
        raise credenciales_invalidas

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada.",
        )

    if usuario.debe_cambiar_contrasena and (request.method, request.url.path) not in PERMITIDO_CON_CONTRASENA_TEMPORAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu contraseña es temporal. Cámbiala en Mi cuenta para poder continuar.",
        )

    return usuario


def require_dueno(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    """Como get_current_user, pero además exige que la cuenta sea dueña de al
    menos un domo.

    Para endpoints exclusivos del dueño: crear/editar/desactivar
    administradores (RF-02, RF-18), configuración del sistema (RF-19), etc. Los
    que actúan sobre un domo comprueban además que sea dueño de ESE domo.
    """
    if usuario.rol != "dueño":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta acción solo puede realizarla el dueño.",
        )
    return usuario
