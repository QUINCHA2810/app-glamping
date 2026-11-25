"""
Endpoints de autenticación — requisito R-1 (RF-01 Inicio de sesión,
RF-02 Creación de cuentas).

POST /auth/login
    Recibe correo y contraseña. Si son correctos y la cuenta está activa,
    devuelve un JWT. Tras 5 fallos en 15 min por IP o por correo responde 429
    (limite_login.py).

POST /auth/registro
    Crea una cuenta de administrador nueva, con la contraseña encriptada
    (passlib + bcrypt). Según RF-02 y `docs/Requisitos/Usuariosypermisos.md`
    (sección 6), el registro NO es un flujo abierto al público: solo el
    dueño autenticado puede llamarlo, y la cuenta creada siempre queda con
    rol 'administrador' (la cuenta del dueño se aprovisiona manualmente en
    Supabase, no por esta API).

    Modelo v2: el cuerpo trae `domo_id`, un domo del que quien llama es dueño, y en
    la misma transacción se crea la asignación 'administrador' a ese domo. Así la
    cuenta nunca queda huérfana: GET /administradores lista los administradores con
    asignación en los domos del dueño (ya no existe `creado_por_id`). 404 si el domo
    no es suyo, 403 si lo ve solo como administrador.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import limite_login
from database import get_db
from models import ROL_ADMINISTRADOR, Asignacion, Usuario
from routers.domos import domo_del_dueno
from schemas import LoginRequest, RegistroRequest, TokenResponse, UsuarioOut
from security import crear_token_acceso, hash_password, require_dueno, verify_password

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/login", response_model=TokenResponse)
def login(datos: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Límite de fallos por IP y por correo (limite_login.py): se mira antes de la
    # contraseña y el 429 es igual exista o no el correo.
    ip = limite_login.ip_cliente(request)
    limite_login.comprobar(ip, datos.correo)

    usuario = db.query(Usuario).filter(Usuario.correo == datos.correo).first()

    if usuario is None or not verify_password(datos.contrasena, usuario.contrasena_hash):
        limite_login.registrar_fallo(ip, datos.correo)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos.",
        )

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada.",
        )

    limite_login.registrar_exito(datos.correo)
    token = crear_token_acceso(usuario)
    return TokenResponse(access_token=token)


@router.post(
    "/registro",
    response_model=UsuarioOut,
    status_code=status.HTTP_201_CREATED,
)
def registro(
    datos: RegistroRequest,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    """Crea una cuenta de administrador. Requiere estar autenticado como
    dueño (ver docstring del módulo)."""
    domo = domo_del_dueno(db, datos.domo_id, dueno)
    ya_existe = db.query(Usuario).filter(Usuario.correo == datos.correo).first()
    if ya_existe is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un usuario con ese correo.",
        )

    nuevo_usuario = Usuario(
        nombre=datos.nombre,
        apellido=datos.apellido,
        correo=datos.correo,
        contrasena_hash=hash_password(datos.contrasena),
        telefono=datos.telefono,
        activo=True,
        # La contraseña la eligió el dueño: hay que cambiarla al entrar.
        debe_cambiar_contrasena=True,
    )
    db.add(nuevo_usuario)
    db.flush()
    db.add(Asignacion(usuario_id=nuevo_usuario.id, domo_id=domo.id, rol=ROL_ADMINISTRADOR))
    db.commit()
    db.refresh(nuevo_usuario)
    return nuevo_usuario
