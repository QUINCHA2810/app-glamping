"""
GET /domos/{domo_id}/categorias

Presets de cada categoría de dispositivo calculados para la batería y el perfil
del domo, y los de los tres perfiles para la vista previa, para que el frontend
no repita la fórmula (ver servicios/categorias.py). Solo
lectura: dueño propietario o administrador con asignación activa, mismo
criterio de _domos_accesibles() que el resto de routers.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario
from routers.domos import domo_accesible
from schemas import CategoriasDomoOut, PerfilPresetsOut, PresetCategoriaOut
from security import get_current_user
from servicios.categorias import (
    ESENCIAL,
    PERFILES,
    RESERVA_SOC_POR_BATERIA,
    perfil_de,
    presets_de,
    tipo_bateria_de,
)

router = APIRouter(prefix="/domos/{domo_id}/categorias", tags=["Categorías de dispositivo"])


def _presets_de(tipo_bateria: str, perfil: str) -> list[PresetCategoriaOut]:
    return [
        PresetCategoriaOut(
            categoria=preset.categoria,
            umbral_apagado_soc=preset.umbral_apagado_soc,
            umbral_reactivacion_soc=preset.umbral_reactivacion_soc,
            prioridad=preset.prioridad,
            corte_automatico=preset.categoria != ESENCIAL,
        )
        for preset in presets_de(tipo_bateria, perfil)
    ]


@router.get("", response_model=CategoriasDomoOut)
def obtener_categorias(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    tipo_bateria = tipo_bateria_de(domo)
    perfil = perfil_de(domo)

    return CategoriasDomoOut(
        domo_id=domo.id,
        tipo_bateria=tipo_bateria,
        # v2: la batería está configurada si tiene capacidad (el tipo siempre tiene valor).
        bateria_configurada=domo.bateria_capacidad_wh is not None,
        reserva_soc=RESERVA_SOC_POR_BATERIA[tipo_bateria],
        reservas_por_bateria=RESERVA_SOC_POR_BATERIA,
        perfil=perfil,
        categorias=_presets_de(tipo_bateria, perfil),
        perfiles=[PerfilPresetsOut(perfil=p, categorias=_presets_de(tipo_bateria, p)) for p in PERFILES],
    )
