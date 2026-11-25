"""
Endpoints de configuración del sistema — requisito R-19 (Configuración
del sistema).

Modelo v2: la configuración son tres columnas de `domos` (`bateria_tipo`,
`bateria_capacidad_wh`, `perfil`); ya no hay tabla configuracion_sistema.

GET /domos/{domo_id}/configuracion
    La configuración del domo, para el dueño o un administrador del domo.
    `es_default` es true si el domo todavía no tiene batería configurada
    (capacidad NULL): el tipo y el perfil son entonces los valores por defecto
    (lifepo4, conservador). `umbral_soc_bajo`/`umbral_soc_recuperacion` son el
    preset confort de la batería y el perfil, calculado: ya no hay umbrales
    globales guardados. `fecha_modificacion` va null.

PUT /domos/{domo_id}/configuracion
    Cambia `bateria` (tipo y capacidad) y/o `perfil_control`. Exclusivo del dueño
    de ESE domo (403 si es administrador de él). Los umbrales de todas las tomas
    cambian solos, porque se calculan (servicios/categorias.py): la respuesta trae
    `tomas_recalculadas` = las tomas no esenciales del domo (activas o no) cuando
    cambia la batería o el perfil, y `tomas_personalizadas` = 0, para que el
    frontend siga mostrando el aviso.

    Ya no se aceptan `umbral_soc_bajo`/`umbral_soc_recuperacion` ni los campos de
    texto libre (`parametros_control`, `limites_alerta`, `prioridades_carga`,
    `configuracion_dispositivos`): 422 con el motivo, en vez de aceptarlos sin
    guardarlos.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Dispositivo, Domo, Usuario
from routers.domos import domo_accesible, domo_del_dueno
from schemas import BateriaConfig, ConfiguracionSistemaOut, ConfiguracionSistemaUpdate
from security import get_current_user, require_dueno
from servicios.categorias import ESENCIAL, perfil_de, tipo_bateria_de, umbrales_globales_de

router = APIRouter(prefix="/domos/{domo_id}/configuracion", tags=["Configuración del sistema"])

CAMPOS_QUE_YA_NO_SE_GUARDAN = {
    "umbral_soc_bajo": "Los umbrales globales ya no se configuran: cada toma usa el de su categoría y el perfil del domo.",
    "umbral_soc_recuperacion": "Los umbrales globales ya no se configuran: cada toma usa el de su categoría y el perfil del domo.",
    "parametros_control": "parametros_control ya no se guarda.",
    "limites_alerta": "limites_alerta ya no se guarda.",
    "prioridades_carga": "prioridades_carga ya no se guarda: la prioridad sale de la categoría.",
    "configuracion_dispositivos": "configuracion_dispositivos ya no se guarda.",
}


def _salida(domo: Domo) -> ConfiguracionSistemaOut:
    tipo, perfil = tipo_bateria_de(domo), perfil_de(domo)
    umbral_bajo, umbral_recuperacion = umbrales_globales_de(tipo, perfil)
    bateria = (
        BateriaConfig(tipo=tipo, capacidad_wh=domo.bateria_capacidad_wh)
        if domo.bateria_capacidad_wh is not None
        else None
    )
    return ConfiguracionSistemaOut(
        domo_id=domo.id,
        es_default=bateria is None,
        umbral_soc_bajo=umbral_bajo,
        umbral_soc_recuperacion=umbral_recuperacion,
        bateria=bateria,
        perfil_control=perfil,
        fecha_modificacion=None,
    )


@router.get("", response_model=ConfiguracionSistemaOut)
def obtener_configuracion(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    return _salida(domo_accesible(db, domo_id, usuario_actual))


@router.put("", response_model=ConfiguracionSistemaOut)
def actualizar_configuracion(
    domo_id: int,
    datos: ConfiguracionSistemaUpdate,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    domo = domo_del_dueno(db, domo_id, dueno)
    cambios = datos.model_dump(exclude_unset=True)

    for campo in cambios:
        if campo in CAMPOS_QUE_YA_NO_SE_GUARDAN:
            raise HTTPException(status_code=422, detail=CAMPOS_QUE_YA_NO_SE_GUARDAN[campo])

    antes = (tipo_bateria_de(domo), perfil_de(domo))
    if "bateria" in cambios:
        domo.bateria_tipo = cambios["bateria"]["tipo"]
        domo.bateria_capacidad_wh = cambios["bateria"]["capacidad_wh"]
    if "perfil_control" in cambios:
        domo.perfil = cambios["perfil_control"]
    despues = (tipo_bateria_de(domo), perfil_de(domo))

    recalculadas = 0
    if despues != antes:
        recalculadas = (
            db.query(Dispositivo)
            .filter(Dispositivo.domo_id == domo.id, Dispositivo.tipo == "toma", Dispositivo.categoria != ESENCIAL)
            .count()
        )

    db.commit()
    db.refresh(domo)
    return _salida(domo).model_copy(update={"tomas_recalculadas": recalculadas, "tomas_personalizadas": 0})
