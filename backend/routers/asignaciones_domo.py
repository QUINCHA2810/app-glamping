"""
Endpoints de asignación de administradores a domos — requisito R-4.

Modelo v2: una asignación es una fila de `asignaciones` (usuario, domo, rol). No hay
estado ni permisos guardados: quitar el acceso es borrar la fila, y volver a darlo,
crearla de nuevo.

POST /domos/{domo_id}/administradores
    Asigna un administrador existente (identificado por su correo) a un domo. Solo
    el dueño de ese domo, y solo con administradores que ya tienen asignado alguno
    de sus domos (en v2 toda cuenta de administrador nace asignada: POST
    /auth/registro). Cualquier otro correo da el mismo 404, para no revelar qué
    cuentas existen. Si ya estaba asignado, responde 200 sin cambiar nada; si no,
    201.

GET /domos/{domo_id}/administradores
    Lista los administradores de ese domo, visible solo para su dueño.

PATCH /domos/{domo_id}/administradores/{administrador_id}
    `estado_asignacion`: 'inactivo' quita la asignación (borra la fila) y 'activo' la
    deja como está. No se puede quitar la ÚLTIMA asignación de una cuenta (409): la
    cuenta quedaría sin domo y ningún dueño podría verla ni volver a asignarla; para
    cortarle el acceso, se desactiva la cuenta (PATCH /administradores/{id}).
    `permisos_asignados` se acepta pero no se guarda.

Todos son exclusivos del dueño de ESE domo (403 si lo ve como administrador, 404 si
no lo ve): routers/domos.py, domo_del_dueno.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from database import get_db
from models import ROL_ADMINISTRADOR, ROL_DUENO, Asignacion, Usuario
from routers.administradores import domos_del_dueno
from routers.domos import domo_del_dueno
from schemas import AdministradorDomoOut, AsignacionUpdateRequest, AsignarAdministradorRequest
from security import require_dueno

router = APIRouter(prefix="/domos/{domo_id}/administradores", tags=["Asignación de administradores a domos"])

ESTADOS_ASIGNACION_VALIDOS = {"activo", "inactivo"}


def _puede_asignarlo(db: Session, dueno: Usuario, administrador: Usuario) -> bool:
    """Una cuenta de administrador (no dueña de ningún domo) que ya tiene asignado
    alguno de los domos de este dueño."""
    if db.query(Asignacion.id).filter(Asignacion.usuario_id == administrador.id, Asignacion.rol == ROL_DUENO).first():
        return False
    return (
        db.query(Asignacion.id)
        .filter(Asignacion.usuario_id == administrador.id, Asignacion.domo_id.in_(domos_del_dueno(dueno)))
        .first()
        is not None
    )


def _salida(asignacion_id: int, domo_id: int, administrador: Usuario, estado: str = "activo") -> AdministradorDomoOut:
    return AdministradorDomoOut(
        id=asignacion_id,
        domo_id=domo_id,
        administrador_id=administrador.id,
        nombre_administrador=administrador.nombre,
        correo_administrador=administrador.correo,
        permisos_asignados=None,
        estado_asignacion=estado,
        fecha_asignacion=None,
    )


@router.post("", response_model=AdministradorDomoOut)
def asignar_administrador(
    domo_id: int,
    datos: AsignarAdministradorRequest,
    response: Response,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    domo = domo_del_dueno(db, domo_id, dueno)

    administrador = db.query(Usuario).filter(Usuario.correo == datos.correo).first()
    if administrador is None or not _puede_asignarlo(db, dueno, administrador):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay ningún administrador tuyo con ese correo.",
        )

    asignacion = (
        db.query(Asignacion)
        .filter(Asignacion.domo_id == domo.id, Asignacion.usuario_id == administrador.id)
        .first()
    )
    if asignacion is None:
        asignacion = Asignacion(domo_id=domo.id, usuario_id=administrador.id, rol=ROL_ADMINISTRADOR)
        db.add(asignacion)
        db.commit()
        db.refresh(asignacion)
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK
    return _salida(asignacion.id, domo.id, administrador)


@router.get("", response_model=list[AdministradorDomoOut])
def listar_administradores_del_domo(
    domo_id: int,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    domo = domo_del_dueno(db, domo_id, dueno)
    filas = (
        db.query(Asignacion, Usuario)
        .join(Usuario, Usuario.id == Asignacion.usuario_id)
        .filter(Asignacion.domo_id == domo.id, Asignacion.rol == ROL_ADMINISTRADOR)
        .order_by(Asignacion.id)
        .all()
    )
    return [_salida(asignacion.id, domo.id, administrador) for asignacion, administrador in filas]


@router.patch("/{administrador_id}", response_model=AdministradorDomoOut)
def actualizar_asignacion(
    domo_id: int,
    administrador_id: int,
    datos: AsignacionUpdateRequest,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    domo = domo_del_dueno(db, domo_id, dueno)

    asignacion = (
        db.query(Asignacion)
        .filter(
            Asignacion.domo_id == domo.id,
            Asignacion.usuario_id == administrador_id,
            Asignacion.rol == ROL_ADMINISTRADOR,
        )
        .first()
    )
    if asignacion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe una asignación entre ese administrador y este domo.",
        )

    estado = datos.model_dump(exclude_unset=True).get("estado_asignacion", "activo")
    if estado not in ESTADOS_ASIGNACION_VALIDOS:
        # 422 literal: el nombre de la constante cambió entre versiones de Starlette.
        raise HTTPException(
            status_code=422,
            detail=f"estado_asignacion debe ser uno de: {sorted(ESTADOS_ASIGNACION_VALIDOS)}.",
        )

    administrador = db.get(Usuario, administrador_id)
    salida = _salida(asignacion.id, domo.id, administrador, estado)
    if estado == "inactivo":
        otras = (
            db.query(Asignacion.id)
            .filter(Asignacion.usuario_id == administrador_id, Asignacion.id != asignacion.id)
            .count()
        )
        if otras == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Es el único domo de este administrador: si se lo quitas, nadie podría volver a verlo. "
                    "Para cortarle el acceso, desactiva su cuenta."
                ),
            )
        db.delete(asignacion)
        db.commit()
    return salida
