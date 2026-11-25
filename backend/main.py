"""
Aplicación FastAPI del backend de Vatia.

Expone el API que consumirá la aplicación (dueño/administrador) para el
monitoreo y control del domo experimental. Incluye la raíz ("/") y el
endpoint de salud ("/health"), que confirman que el servicio está arriba y
que la conexión a la base de datos (Supabase/Postgres, ver database.py)
responde correctamente, además de los endpoints de autenticación
(routers/auth.py — requisito R-1), perfil de usuario (routers/users.py —
requisito R-2), gestión de domos (routers/domos.py — requisito R-3),
asignación de administradores a domos (routers/asignaciones_domo.py —
requisito R-4), monitoreo de generación fotovoltaica
(routers/lecturas_fotovoltaicas.py — requisito R-6), monitoreo de
consumo energético (routers/lecturas_consumo.py — requisito R-7),
monitoreo del sistema de almacenamiento (routers/lecturas_bateria.py —
requisito R-8), monitoreo de sensores (routers/lecturas_sensores.py —
requisito R-9), control/estado de dispositivos — iluminación y tomas
inteligentes (routers/dispositivos.py — requisitos R-10, R-11, R-12),
presets de categorías de dispositivo (routers/categorias.py) y control
energético / algoritmo de supervivencia
(routers/control_energetico.py — requisito R-13), historial de acciones
(routers/historial.py — requisito R-16, solo lectura), sistema de
alertas (routers/alertas.py — requisito R-15), estado de comunicación
(routers/estado_comunicacion.py — requisito R-17), listado de
administradores propios (routers/administradores.py — requisito R-18),
configuración del sistema (routers/configuracion.py — requisito R-19),
estado general del domo (routers/estado_general.py — requisito R-5,
solo lectura), visualización de históricos (routers/historico.py —
requisito R-14, solo lectura y agregados), reportes (routers/reportes.py) y
clima de referencia de Open-Meteo (routers/clima.py, solo lectura).

Los demás endpoints funcionales se agregarán sobre esta base a medida
que se definan formalmente sus requisitos y contratos, siguiendo las
reglas de docs/arquitectura_sistema.md.
"""

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from errores_validacion import manejar_error_validacion
from json_finito import RechazarNumerosNoFinitos
from publicacion import CabecerasSeguridad, opciones_documentacion
import models  # noqa: F401  (registra los modelos ORM sobre database.Base)
from routers import administradores as administradores_router
from routers import alertas as alertas_router
from routers import asignaciones_domo as asignaciones_domo_router
from routers import auth as auth_router
from routers import categorias as categorias_router
from routers import clima as clima_router
from routers import configuracion as configuracion_router
from routers import control_energetico as control_energetico_router
from routers import dispositivos as dispositivos_router
from routers import domos as domos_router
from routers import energia_reciente as energia_reciente_router
from routers import estado_comunicacion as estado_comunicacion_router
from routers import estado_general as estado_general_router
from routers import historial as historial_router
from routers import historico as historico_router
from routers import iluminacion as iluminacion_router
from routers import lecturas_bateria as lecturas_bateria_router
from routers import lecturas_consumo as lecturas_consumo_router
from routers import lecturas_fotovoltaicas as lecturas_fotovoltaicas_router
from routers import lecturas_sensores as lecturas_sensores_router
from routers import reportes as reportes_router
from routers import soc_recalibracion as soc_recalibracion_router
from routers import users as users_router

load_dotenv()

# Con API_DOCS=false (modo público, docs/publicacion_web.md) no hay /docs, /redoc
# ni /openapi.json.
app = FastAPI(
    title="Vatia API",
    description=(
        "Backend del sistema de gestión y monitoreo energético "
        "para el domo experimental."
    ),
    version="0.1.0",
    **opciones_documentacion(),
)

app.add_exception_handler(RequestValidationError, manejar_error_validacion)

# CORS para el frontend web (frontend/, Vite). Orígenes permitidos desde
# CORS_ORIGINS (separados por comas). allow_credentials=False: el JWT
# viaja en el header Authorization, no en cookies.
CORS_ORIGINS = [
    origen.strip()
    for origen in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origen.strip()
]

# Se agrega antes que CORS para quedar por dentro: su 422 también lleva las
# cabeceras CORS, y sin ellas el navegador no dejaría leer el error.
app.add_middleware(RechazarNumerosNoFinitos)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# La última en agregarse queda por fuera: también llegan a las respuestas de CORS y
# a los 422 de RechazarNumerosNoFinitos.
app.add_middleware(CabecerasSeguridad)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(domos_router.router)
app.include_router(asignaciones_domo_router.router)
app.include_router(dispositivos_router.router)
app.include_router(categorias_router.router)
app.include_router(iluminacion_router.router)
app.include_router(lecturas_fotovoltaicas_router.router)
app.include_router(lecturas_consumo_router.router)
app.include_router(lecturas_bateria_router.router)
app.include_router(lecturas_sensores_router.router)
app.include_router(control_energetico_router.router)
app.include_router(historial_router.router)
app.include_router(alertas_router.router)
app.include_router(estado_comunicacion_router.router)
app.include_router(administradores_router.router)
app.include_router(configuracion_router.router)
app.include_router(estado_general_router.router)
app.include_router(energia_reciente_router.router)
app.include_router(historico_router.router)
app.include_router(clima_router.router)
app.include_router(reportes_router.router)
app.include_router(soc_recalibracion_router.router)


@app.get("/")
def root():
    """Información básica del API."""
    return {
        "name": app.title,
        "version": app.version,
        "status": "running",
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """
    Verifica que el servicio FastAPI está arriba y que la conexión a la
    base de datos (Supabase/Postgres) responde. No derriba el servicio si
    la base de datos falla: reporta el estado en la respuesta.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "api": "up",
                "database": "error",
                "detail": str(exc),
            },
        )

    return {
        "status": "ok",
        "api": "up",
        "database": "connected",
    }


if __name__ == "__main__":
    import uvicorn

    # Solo en este equipo por defecto: el frontend llega por el proxy de Vite
    # (docs/acceso_celular.md) y el ESP32 habla por MQTT, no con el API. Con
    # 0.0.0.0 el puerto 8000 quedaría al alcance de la red del domo y de la tailnet.
    uvicorn.run("main:app", host=os.getenv("API_HOST", "127.0.0.1"), port=8000, reload=True)
