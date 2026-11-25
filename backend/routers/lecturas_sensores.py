"""
Endpoints de lecturas — requisito R-9 (monitoreo de sensores).

Modelo v2: las lecturas viven en la tabla `mediciones` (una fila por domo y bloque
de 10 s). Las rutas, los cuerpos y las respuestas son los de v1: ver
routers/lecturas.py y servicios/mediciones.py.
"""

from routers.lecturas import crear_router
from schemas import LecturaSensorCreate, LecturaSensorOut

router = crear_router(
    "sensores",
    "lecturas-sensores",
    "Lecturas de sensores",
    LecturaSensorCreate,
    LecturaSensorOut,
    "Este domo no tiene lecturas de sensores registradas.",
)
