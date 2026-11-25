"""
Endpoints de lecturas — requisito R-6 (monitoreo de la generación fotovoltaica).

Modelo v2: las lecturas viven en la tabla `mediciones` (una fila por domo y bloque
de 10 s). Las rutas, los cuerpos y las respuestas son los de v1: ver
routers/lecturas.py y servicios/mediciones.py.
"""

from routers.lecturas import crear_router
from schemas import LecturaFotovoltaicaCreate, LecturaFotovoltaicaOut

router = crear_router(
    "fotovoltaica",
    "lecturas-fotovoltaicas",
    "Lecturas fotovoltaicas",
    LecturaFotovoltaicaCreate,
    LecturaFotovoltaicaOut,
    "Este domo no tiene lecturas fotovoltaicas registradas.",
)
