"""
GET /domos/{domo_id}/reportes/resumen?desde=AAAA-MM-DD&hasta=AAAA-MM-DD

Resumen del rango, con ambas fechas incluidas y en hora de Bogotá. Se calcula con
consultas agregadas en servicios/reportes.py, donde están las reglas. Acceso con
_domos_accesibles: dueño propietario o administrador con asignación activa.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Domo, Usuario
from routers.domos import DOMO_NO_ENCONTRADO, _domos_accesibles
from schemas import ReporteResumenOut
from security import get_current_user
from servicios.reportes import resumen_del_domo

router = APIRouter(prefix="/domos/{domo_id}/reportes", tags=["Reportes"])

# La respuesta trae una fila por día del rango.
DIAS_MAXIMOS = 366


@router.get("/resumen", response_model=ReporteResumenOut)
def obtener_resumen(
    domo_id: int,
    desde: date,
    hasta: date,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domos_accesibles(db, usuario_actual).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    if desde > hasta:
        raise HTTPException(status_code=422, detail="La fecha inicial no puede ser posterior a la final.")
    if hasta - desde > timedelta(days=DIAS_MAXIMOS - 1):
        raise HTTPException(status_code=422, detail=f"El rango no puede superar {DIAS_MAXIMOS} días.")
    return resumen_del_domo(db, domo.id, desde, hasta)
