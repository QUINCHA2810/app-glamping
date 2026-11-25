"""
Endpoints de gestión de domos — requisito R-3 (RF-03 Gestión de domos,
RF-04 Acceso del administrador) — y la regla de acceso de todo el backend.

POST /domos
    Crea un domo nuevo. Solo un dueño (de al menos un domo) puede hacerlo. En la
    misma transacción crea la asignación 'dueno' del usuario autenticado: un domo
    nunca queda sin dueño.

GET /domos
    Lista los domos donde el usuario autenticado tiene una asignación, sea como
    dueño o como administrador.

GET /domos/{domo_id}
    Devuelve un domo puntual, solo si el usuario tiene acceso. Si no, 404 en vez
    de 403, para no revelar que el domo existe.

PUT /domos/{domo_id}
    Actualiza nombre y ubicación. Solo el dueño de ESE domo (403 si es
    administrador de él).

Modelo v2: el acceso sale SOLO de la tabla `asignaciones` (usuario, domo, rol). Un
mismo usuario puede ser dueño de un domo y administrador de otro: el rol se mira
siempre en el domo de la URL (rol_en_domo), nunca el "rol de la cuenta".

Nota sobre RLS (ver docs/arquitectura_sistema.md, "Corrección sobre RLS"): el backend usa
autenticación propia (JWT), así que TODO el filtrado por domo vive en estas
consultas — ninguna confía en que la base filtre nada por su cuenta.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Query, Session

from database import get_db
from models import ROL_DUENO, Asignacion, Domo, Usuario
from schemas import DomoCreate, DomoOut, DomoUpdate
from security import get_current_user, require_dueno

router = APIRouter(prefix="/domos", tags=["Domos"])

DOMO_NO_ENCONTRADO = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Domo no encontrado.",
)

SOLO_EL_DUENO = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Esta acción solo puede realizarla el dueño.",
)


def _domos_accesibles(db: Session, usuario: Usuario) -> Query:
    """Query base con los domos que el usuario autenticado puede ver: los que
    tienen una asignación suya, con cualquier rol.

    Es el único lugar donde se decide "qué domos ve quién" — el resto del
    backend la reutiliza en vez de repetir el filtro."""
    return db.query(Domo).join(Asignacion, Asignacion.domo_id == Domo.id).filter(Asignacion.usuario_id == usuario.id)


def rol_en_domo(db: Session, usuario: Usuario, domo_id: int) -> Optional[str]:
    """'dueno', 'administrador' o None si el usuario no tiene ese domo."""
    return (
        db.query(Asignacion.rol)
        .filter(Asignacion.usuario_id == usuario.id, Asignacion.domo_id == domo_id)
        .scalar()
    )


def es_dueno_de(db: Session, usuario: Usuario, domo_id: int) -> bool:
    return rol_en_domo(db, usuario, domo_id) == ROL_DUENO


def domo_accesible(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo con acceso (dueño o administrador de él), o 404."""
    domo = _domos_accesibles(db, usuario).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    return domo


def domo_del_dueno(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo del que el usuario es DUEÑO. 404 si no lo puede ver; 403 si lo ve como
    administrador (igual que v1 respondía a un administrador en una acción del dueño)."""
    domo = domo_accesible(db, domo_id, usuario)
    if not es_dueno_de(db, usuario, domo.id):
        raise SOLO_EL_DUENO
    return domo


@router.post("", response_model=DomoOut, status_code=status.HTTP_201_CREATED)
def crear_domo(
    datos: DomoCreate,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    nuevo_domo = Domo(nombre=datos.nombre, ubicacion=datos.ubicacion)
    db.add(nuevo_domo)
    db.flush()
    db.add(Asignacion(usuario_id=dueno.id, domo_id=nuevo_domo.id, rol=ROL_DUENO))
    db.commit()
    db.refresh(nuevo_domo)
    return nuevo_domo


@router.get("", response_model=list[DomoOut])
def listar_domos(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    return _domos_accesibles(db, usuario_actual).order_by(Domo.id).all()


@router.get("/{domo_id}", response_model=DomoOut)
def obtener_domo(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    return domo_accesible(db, domo_id, usuario_actual)


@router.put("/{domo_id}", response_model=DomoOut)
def actualizar_domo(
    domo_id: int,
    datos: DomoUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    if not es_dueno_de(db, usuario_actual, domo.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Por ahora solo el dueño del domo puede editarlo.",
        )

    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(domo, campo, valor)

    db.commit()
    db.refresh(domo)
    return domo
