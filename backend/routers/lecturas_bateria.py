"""
Endpoints de lecturas — requisito R-8 (monitoreo del sistema de almacenamiento).

Modelo v2: las lecturas viven en la tabla `mediciones` (una fila por domo y bloque
de 10 s). Las rutas, los cuerpos y las respuestas son los de v1: ver
routers/lecturas.py y servicios/mediciones.py.
"""

from routers.lecturas import crear_router
from schemas import LecturaBateriaCreate, LecturaBateriaOut

router = crear_router(
    "bateria",
    "lecturas-bateria",
    "Lecturas de batería",
    LecturaBateriaCreate,
    LecturaBateriaOut,
    "Este domo no tiene lecturas de batería registradas.",
)
