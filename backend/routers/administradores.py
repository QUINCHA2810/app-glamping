"""
Gestión de cuentas de administrador — R-18. Exclusivo de dueños.

Cubren el resto de R-18:
- POST /auth/registro (routers/auth.py) crea la cuenta, ya asignada a un domo.
- GET /users/me (routers/users.py) muestra el propio perfil.
- POST y PATCH /domos/{domo_id}/administradores (routers/asignaciones_domo.py)
  asignan un domo o quitan la asignación.

Modelo v2: ya no existe `creado_por_id`. Lo que ve y puede hacer un dueño sale de
las asignaciones:

GET /administradores
    Los administradores con alguna asignación en los domos de este dueño, con esas
    asignaciones. No expone nada de los domos de otros dueños.

PATCH /administradores/{administrador_id}
    Edita nombre, apellido y teléfono, y activa o desactiva la cuenta. Solo sobre
    las cuentas "propias" (`cuenta_propia`): las que solo tienen asignaciones en
    domos de este dueño y no son dueñas de ningún domo. Una cuenta que también
    atiende domos de otro dueño no la puede bloquear uno solo de ellos (lo mismo
    que en v1 protegía `creado_por_id`). 404 en vez de 403 si no es propia. Una
    cuenta desactivada no puede iniciar sesión, y sus tokens dejan de servir en la
    siguiente petición (security.get_current_user consulta la cuenta cada vez).

POST /administradores/{administrador_id}/restablecer-contrasena
    Pone una contraseña temporal y cierra las sesiones abiertas de esa cuenta, que al
    entrar tendrá que cambiarla (usuarios.debe_cambiar_contrasena). Solo sobre las
    cuentas propias, igual que el PATCH. La contraseña no se devuelve.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import ROL_ADMINISTRADOR, ROL_DUENO, Asignacion, Domo, Usuario
from schemas import AdministradorPropioOut, AdministradorUpdate, DomoDeAdministradorOut, RestablecerContrasenaRequest
from security import cambiar_contrasena, require_dueno

router = APIRouter(prefix="/administradores", tags=["Administradores"])


def domos_del_dueno(dueno: Usuario):
    """Subconsulta con los id de los domos de los que `dueno` es dueño."""
    return select(Asignacion.domo_id).where(Asignacion.usuario_id == dueno.id, Asignacion.rol == ROL_DUENO)


def es_cuenta_propia(db: Session, dueno: Usuario, administrador: Usuario) -> bool:
    """Todas sus asignaciones son de administrador en domos de este dueño, y tiene
    al menos una."""
    asignaciones = db.query(Asignacion).filter(Asignacion.usuario_id == administrador.id).all()
    if not asignaciones:
        return False
    mios = set(db.execute(domos_del_dueno(dueno)).scalars())
    return all(a.rol == ROL_ADMINISTRADOR and a.domo_id in mios for a in asignaciones)


def _salidas(db: Session, dueno: Usuario, administradores: list[Usuario]) -> list[AdministradorPropioOut]:
    filas = (
        db.query(Asignacion, Domo)
        .join(Domo, Domo.id == Asignacion.domo_id)
        .filter(Asignacion.rol == ROL_ADMINISTRADOR, Asignacion.domo_id.in_(domos_del_dueno(dueno)))
        .order_by(Domo.id)
        .all()
    )
    domos_por_administrador: dict[int, list[DomoDeAdministradorOut]] = {}
    for asignacion, domo in filas:
        domos_por_administrador.setdefault(asignacion.usuario_id, []).append(
            DomoDeAdministradorOut(domo_id=domo.id, nombre_domo=domo.nombre, estado_asignacion="activo")
        )
    return [
        AdministradorPropioOut(
            id=admin.id,
            nombre=admin.nombre,
            apellido=admin.apellido,
            correo=admin.correo,
            telefono=admin.telefono,
            estado_cuenta=admin.estado_cuenta,
            fecha_creacion=admin.fecha_creacion,
            cuenta_propia=es_cuenta_propia(db, dueno, admin),
            domos=domos_por_administrador.get(admin.id, []),
        )
        for admin in administradores
    ]


@router.get("", response_model=list[AdministradorPropioOut])
def listar_mis_administradores(
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    asignados = select(Asignacion.usuario_id).where(
        Asignacion.rol == ROL_ADMINISTRADOR, Asignacion.domo_id.in_(domos_del_dueno(dueno))
    )
    administradores = (
        db.query(Usuario)
        .filter(Usuario.id.in_(asignados), Usuario.id != dueno.id)
        .order_by(Usuario.creado_en.desc(), Usuario.id.desc())
        .all()
    )
    return _salidas(db, dueno, administradores)


def _cuenta_del_dueno(db: Session, dueno: Usuario, administrador_id: int) -> Usuario:
    administrador = db.get(Usuario, administrador_id)
    if administrador is None or administrador.id == dueno.id or not es_cuenta_propia(db, dueno, administrador):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Administrador no encontrado.")
    return administrador


@router.post("/{administrador_id}/restablecer-contrasena", status_code=status.HTTP_204_NO_CONTENT)
def restablecer_contrasena(
    administrador_id: int,
    datos: RestablecerContrasenaRequest,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
) -> None:
    administrador = _cuenta_del_dueno(db, dueno, administrador_id)
    cambiar_contrasena(administrador, datos.contrasena_temporal)
    administrador.debe_cambiar_contrasena = True
    db.commit()


@router.patch("/{administrador_id}", response_model=AdministradorPropioOut)
def actualizar_administrador(
    administrador_id: int,
    datos: AdministradorUpdate,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    administrador = _cuenta_del_dueno(db, dueno, administrador_id)

    # estado_cuenta es una propiedad de Usuario que escribe `activo`.
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(administrador, campo, valor)
    db.commit()
    db.refresh(administrador)
    return _salidas(db, dueno, [administrador])[0]
