"""
Endpoints de lecturas — requisito R-7 (monitoreo del consumo energético).

Modelo v2: las lecturas viven en la tabla `mediciones` (una fila por domo y bloque
de 10 s). Las rutas, los cuerpos y las respuestas son los de v1: ver
routers/lecturas.py y servicios/mediciones.py.
"""

from routers.lecturas import crear_router
from schemas import LecturaConsumoCreate, LecturaConsumoOut

router = crear_router(
    "consumo",
    "lecturas-consumo",
    "Lecturas de consumo",
    LecturaConsumoCreate,
    LecturaConsumoOut,
    "Este domo no tiene lecturas de consumo registradas.",
)
