"""
Routers de lecturas — requisitos R-6 (fotovoltaica), R-7 (consumo), R-8 (batería)
y R-9 (sensores) — sobre la tabla `mediciones` del modelo v2.

Las rutas y las formas de respuesta son las de v1 (el frontend usa
/lecturas-bateria, /lecturas-fotovoltaicas y /lecturas-consumo):

POST .../lecturas-<tema>
    Guarda una lectura con la misma regla del listener MQTT
    (servicios/mediciones.guardar_medicion): cae en el bloque de 10 s de su
    `fecha_hora` (o de la hora del servidor), reemplaza los valores instantáneos de
    su tema en ese bloque y SUMA la energía del intervalo. Devuelve la lectura
    tal como quedó guardada en el bloque. Dueño o administrador del domo.

GET .../lecturas-<tema>
    Las filas de `mediciones` con algún dato de ese tema, de la más reciente a la
    más antigua, con límite (100) y rango de fechas opcional. `id` es el de la fila
    de mediciones; `fecha_hora`, el inicio del bloque.

GET .../lecturas-<tema>/ultima
    La más reciente, o 404 si no hay ninguna.

En sensores, cada fila de mediciones da una lectura por variable con dato
(frecuencia y factor de potencia del PZEM), que comparten el `id` de la fila.

Acceso: dueño o administrador del domo (routers/domos.py). Sin acceso, 404.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario
from routers.domos import domo_accesible
from security import get_current_user
from servicios import mediciones


def crear_router(tema: str, ruta: str, etiqueta: str, esquema_entrada, esquema_salida, sin_lecturas: str) -> APIRouter:
    router = APIRouter(prefix=f"/domos/{{domo_id}}/{ruta}", tags=[etiqueta])
    es_sensor = tema == "sensores"

    def a_salida(medicion) -> list[dict]:
        return mediciones.como_lecturas_de_sensor(medicion) if es_sensor else [mediciones.como_lectura(medicion, tema)]

    @router.post("", response_model=esquema_salida, status_code=status.HTTP_201_CREATED)
    def registrar_lectura(
        domo_id: int,
        datos: esquema_entrada,
        db: Session = Depends(get_db),
        usuario_actual: Usuario = Depends(get_current_user),
    ):
        domo = domo_accesible(db, domo_id, usuario_actual)
        valores = datos.model_dump(exclude={"fecha_hora"})
        try:
            bloque = mediciones.guardar_medicion(db, domo.id, tema, valores, datos.fecha_hora)
        except mediciones.MedicionInvalida as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        db.commit()
        medicion = mediciones.medicion_del_bloque(db, domo.id, bloque)
        if es_sensor:
            # La lectura de la variable que llegó, aunque haya llegado sin valor.
            columna, unidad = mediciones.VARIABLES_SENSOR[datos.variable_medida]
            return {
                "id": medicion.id,
                "domo_id": medicion.domo_id,
                "tipo_sensor": mediciones.TIPO_SENSOR_PZEM,
                "variable_medida": datos.variable_medida,
                "valor": getattr(medicion, columna),
                "unidad_medida": unidad,
                "fecha_hora": medicion.fecha_hora,
            }
        return a_salida(medicion)[0]

    @router.get("", response_model=list[esquema_salida])
    def listar_lecturas(
        domo_id: int,
        db: Session = Depends(get_db),
        usuario_actual: Usuario = Depends(get_current_user),
        limite: int = Query(100, ge=1, le=1000),
        fecha_desde: Optional[datetime] = None,
        fecha_hasta: Optional[datetime] = None,
    ):
        domo = domo_accesible(db, domo_id, usuario_actual)
        filas = mediciones.consulta_de(db, domo.id, tema, fecha_desde, fecha_hasta).limit(limite).all()
        return [lectura for fila in filas for lectura in a_salida(fila)][:limite]

    @router.get("/ultima", response_model=esquema_salida)
    def obtener_ultima_lectura(
        domo_id: int,
        db: Session = Depends(get_db),
        usuario_actual: Usuario = Depends(get_current_user),
    ):
        domo = domo_accesible(db, domo_id, usuario_actual)
        medicion = mediciones.ultima(db, domo.id, tema)
        if medicion is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=sin_lecturas)
        return a_salida(medicion)[0]

    return router
