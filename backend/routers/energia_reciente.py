"""
GET /domos/{domo_id}/energia-reciente — autonomía (potencia media de 15 min) y flujo de
energía (promedio de 60 s) para el Panel. Solo lectura; el cálculo está en
servicios/energia_reciente.py. Una consulta de ~100 filas por petición, cubierta por la
UNIQUE (domo_id, fecha_hora) de mediciones.

Acceso: el de las mediciones (routers.domos.domo_accesible: dueño o administrador del
domo; si no, 404).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario
from routers.domos import domo_accesible
from schemas import EnergiaRecienteOut
from security import get_current_user
from servicios import energia_reciente, mediciones
from servicios.categorias import reserva_de

router = APIRouter(prefix="/domos/{domo_id}/energia-reciente", tags=["Estado general del domo"])


@router.get("", response_model=EnergiaRecienteOut)
def obtener_energia_reciente(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    ahora = mediciones.ahora_utc()
    filas = energia_reciente.leer_filas(db, domo.id, ahora)
    return {"domo_id": domo.id, **energia_reciente.energia_reciente(filas, ahora, domo.bateria_capacidad_wh, reserva_de(domo))}
