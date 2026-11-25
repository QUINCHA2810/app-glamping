"""
GET /iluminacion/opciones

Lo que el frontend necesita para controlar un bombillo: colores (con el "#rrggbb" de su
tono), temperaturas de color, escenas y presets. Las listas están solo en
servicios/iluminacion.py; aquí no se repiten. Solo lectura y sin datos de ningún domo:
basta con una sesión iniciada.
"""

from fastapi import APIRouter, Depends

from models import Usuario
from schemas import OpcionesIluminacionOut, OpcionIluminacionOut, PresetIluminacionOut
from security import get_current_user
from servicios.iluminacion import COLORES, ESCENAS, PRESETS, SE_ENVIAN_AL_BOMBILLO, TEMPERATURAS_COLOR, TONOS_COLOR, hs_a_hex

router = APIRouter(prefix="/iluminacion", tags=["Dispositivos"])


@router.get("/opciones", response_model=OpcionesIluminacionOut)
def obtener_opciones_iluminacion(_: Usuario = Depends(get_current_user)):
    return OpcionesIluminacionOut(
        colores=[
            OpcionIluminacionOut(valor=valor, etiqueta=etiqueta, hex=hs_a_hex(*TONOS_COLOR[valor]))
            for valor, etiqueta in COLORES
        ],
        temperaturas_color=[OpcionIluminacionOut(valor=valor, etiqueta=etiqueta) for valor, etiqueta in TEMPERATURAS_COLOR],
        escenas=[OpcionIluminacionOut(valor=valor, etiqueta=etiqueta) for valor, etiqueta in ESCENAS],
        presets=[PresetIluminacionOut(**preset) for preset in PRESETS],
        se_envian_al_bombillo=SE_ENVIAN_AL_BOMBILLO,
    )
