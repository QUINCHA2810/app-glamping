"""
Endpoints de perfil de usuario — requisito R-2 (Gestión del perfil).

GET /users/me
    Devuelve el perfil del usuario autenticado.

PUT /users/me
    Permite al usuario autenticado actualizar su propio nombre, apellido,
    y teléfono (v2 no guarda fotografía). Deliberadamente NO permite cambiar correo, rol
    ni estado_cuenta desde aquí (ver PerfilUpdateRequest en schemas.py):
    esos campos los gestiona el dueño sobre otras cuentas (RF-18), nunca
    el propio usuario sobre sí mismo.

POST /users/me/contrasena
    Cambia la propia contraseña, pidiendo la actual. Cierra las demás sesiones de la
    cuenta y devuelve un token nuevo para que esta siga abierta. Una contraseña actual
    incorrecta da 400 y no 401: la sesión es válida, y el frontend cierra la sesión con
    cualquier 401. Quita la obligación de cambiar una contraseña temporal.

Con una contraseña temporal, GET /users/me y este POST son lo único que responde: el
resto da 403 (ver security.get_current_user).

Estos endpoints operan siempre sobre la fila del usuario identificado por
el JWT (get_current_user, de security.py — R-1); nunca reciben un id de
usuario por parámetro, así que no hace falta el filtrado por domo descrito
en la sección "Decisión de arquitectura: multi-tenencia" de docs/arquitectura_sistema.md
— aquí no hay forma de leer o modificar datos de otro usuario.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario
from schemas import CambiarContrasenaRequest, PerfilUpdateRequest, TokenResponse, UsuarioOut
from security import cambiar_contrasena, crear_token_acceso, get_current_user, verify_password

router = APIRouter(prefix="/users", tags=["Perfil de usuario"])


@router.get("/me", response_model=UsuarioOut)
def obtener_perfil(usuario_actual: Usuario = Depends(get_current_user)):
    return usuario_actual


@router.post("/me/contrasena", response_model=TokenResponse)
def cambiar_mi_contrasena(
    datos: CambiarContrasenaRequest,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    if not verify_password(datos.contrasena_actual, usuario_actual.contrasena_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es correcta.")
    if datos.contrasena_nueva == datos.contrasena_actual:
        raise HTTPException(status_code=422, detail="La contraseña nueva debe ser distinta de la actual.")

    cambiar_contrasena(usuario_actual, datos.contrasena_nueva)
    usuario_actual.debe_cambiar_contrasena = False
    db.commit()
    db.refresh(usuario_actual)
    return TokenResponse(access_token=crear_token_acceso(usuario_actual))


@router.put("/me", response_model=UsuarioOut)
def actualizar_perfil(
    datos: PerfilUpdateRequest,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    # exclude_unset: solo toca los campos que el cliente envió realmente,
    # así se puede actualizar por ejemplo solo el teléfono sin tener que
    # reenviar nombre/apellido/telefono.
    cambios = datos.model_dump(exclude_unset=True)
    for campo, valor in cambios.items():
        setattr(usuario_actual, campo, valor)

    db.commit()
    db.refresh(usuario_actual)
    return usuario_actual
