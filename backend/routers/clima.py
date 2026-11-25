"""
Clima de referencia de un domo (servicios/clima.py): estimaciones de Open-Meteo para sus
coordenadas, no mediciones en el domo. Solo lectura; lo escribe el listener.

GET /domos/{domo_id}/clima/actual
    La última estimación y su antigüedad. Sin coordenadas o sin datos: 200 con
    disponible=false y el motivo (la tarjeta lo explica), no 404.

GET /domos/{domo_id}/clima?fecha_desde=...&fecha_hasta=...
    Las estimaciones del rango (inclusive), en orden de fecha. Mismos nombres de
    parámetros que /historico y /lecturas-*. Máximo 31 días (~3000 filas).

Acceso: el de las mediciones (routers.domos.domo_accesible: dueño o administrador del
domo; si no, 404).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Clima, Usuario
from routers.domos import domo_accesible
from schemas import ClimaActualOut, ClimaOut
from security import get_current_user
from servicios.clima import antiguedad_s
from servicios.mediciones import a_utc

router = APIRouter(prefix="/domos/{domo_id}/clima", tags=["Clima"])

RANGO_MAXIMO = timedelta(days=31)


@router.get("/actual", response_model=ClimaActualOut)
def clima_actual(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    if domo.latitud is None or domo.longitud is None:
        return ClimaActualOut(domo_id=domo.id, disponible=False, motivo="sin_coordenadas")
    fila = (
        db.query(Clima).filter(Clima.domo_id == domo.id).order_by(Clima.fecha_hora.desc(), Clima.id.desc()).first()
    )
    if fila is None:
        return ClimaActualOut(domo_id=domo.id, disponible=False, motivo="sin_datos")
    return ClimaActualOut(
        domo_id=domo.id, disponible=True, clima=ClimaOut.model_validate(fila), antiguedad_s=antiguedad_s(fila)
    )


@router.get("", response_model=list[ClimaOut])
def serie_de_clima(
    domo_id: int,
    fecha_desde: datetime = Query(..., description="Inicio del rango (inclusive). Sin zona = UTC."),
    fecha_hasta: datetime = Query(..., description="Fin del rango (inclusive). Sin zona = UTC."),
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    desde, hasta = a_utc(fecha_desde), a_utc(fecha_hasta)
    if desde > hasta:
        raise HTTPException(status_code=422, detail="fecha_desde no puede ser posterior a fecha_hasta.")
    if hasta - desde > RANGO_MAXIMO:
        raise HTTPException(status_code=422, detail="El rango no puede pasar de 31 días.")
    return (
        db.query(Clima)
        .filter(Clima.domo_id == domo.id, Clima.fecha_hora >= desde, Clima.fecha_hora <= hasta)
        .order_by(Clima.fecha_hora, Clima.id)
        .all()
    )
