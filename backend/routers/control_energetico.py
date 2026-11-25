"""
Endpoints de control energético — requisito R-13 ("Instinto de
Supervivencia"): apagar tomas no esenciales cuando la batería está baja,
y reactivarlas cuando se recupera, preservando siempre la iluminación
(los bombillos nunca se tocan).

Umbrales por toma, con corte escalonado:

    Cada toma tiene su umbral de apagado y de reactivación, CALCULADOS (modelo v2)
    con su categoría y el tipo de batería y el perfil del domo
    (servicios/categorias.py): no se guardan ni se editan. Como cada categoría
    tiene un par distinto, en una misma llamada es normal que unas tomas se
    apaguen y otras se reactiven a la vez.

    Ejemplo (LiFePO4, conservador): prescindible 50/60, confort 35/45, importante
    20/30. Con el SoC bajando, en 49 % se apaga la prescindible; en 34 %, la
    confort; en 19 %, la importante. Cambiar el perfil del domo cambia los
    umbrales en la siguiente evaluación, sin tocar los dispositivos.

    Para cada toma ENCENDIDA: se apaga si SoC actual < SU umbral de
    apagado efectivo. Excepción (primacía humana, DB-4): si su última acción es
    un encendido de una persona y desde entonces el SoC no ha llegado a su umbral
    de reactivación, se respeta, salvo con el SoC bajo la reserva de la batería
    (entonces se corta con motivo=reserva). Ver servicios/control_energetico.py.
    Para cada toma APAGADA POR EL SISTEMA (ver _tomas_apagadas_por_sistema
    — su última acción es un apagado con actor_tipo='sistema'):
    se reactiva si SoC actual >= SU umbral de reactivación efectivo.

    Orden de ejecución dentro de una misma llamada: todos los apagados
    primero, en prioridad ascendente (menos esencial primero); luego todas las
    reactivaciones, en prioridad descendente (más esencial primero). La
    prioridad sale de la categoría. A igual prioridad, por id.

    Categorías (servicios/categorias.py): R-13 nunca corta una toma esencial
    e ignora las inactivas (activo=false).

Efecto sobre el hardware real — ya NO es solo una simulación. Cada
apagado o reactivación que este algoritmo decide sobre una toma con
`dispositivos.id_fisico` no nulo se publica además por MQTT en
`domo/{domo_id}/comando/toma` con origen="r13", y el ESP32 conmuta la
toma Tuya real (ver backend/mqtt_publisher.py y firmware/domo_nodo/).

    Las tomas con `id_fisico` NULL siguen siendo SIMULADAS: se les
    cambia la fila en la base de datos y se les registra la acción en
    la tabla acciones exactamente igual, pero no se publica nada — así
    las tomas de prueba no mueven hardware. Los bombillos NUNCA se
    tocan por esta vía, ni física ni lógicamente (este algoritmo solo
    mira tipo='toma', es el requisito de preservar la iluminación).

    El orden es siempre el mismo: primero el db.commit(), después la
    publicación. La base de datos es la fuente de verdad del estado
    deseado; MQTT es el efecto secundario que lo propaga. Si el broker
    está caído, la publicación falla en silencio (queda en el log) y la
    respuesta HTTP no cambia: el estado ya quedó guardado. Conciliar ese
    estado con el que el ESP32 reporta en `domo/{domo_id}/estado/toma`
    es trabajo pendiente aparte.

En producción, la DECISIÓN de apagar también deberá correr localmente en
el ESP32 (decisión de diseño: el domo no puede depender de tener
Internet para protegerse de un apagón por batería baja). Lo que este
router ejecuta hoy es la lógica desde el backend, con efecto real sobre
la toma.

`acciones.condicion` no tiene un campo "modo" dedicado
en el esquema de la base de datos (ver models.py) — se codifica como
texto libre ("modo=supervivencia; soc=<valor>") en ese campo Text, tanto
para apagados como para reactivaciones: ambas direcciones pertenecen al
mismo algoritmo de supervivencia.

POST .../control-energetico/evaluar
    Ejecuta la lógica descrita arriba con el SoC actual del domo. 409 si no
    hay ningún SoC de los últimos 2 minutos (mediciones.VIGENCIA_S): con datos
    viejos o en null R-13 no corta ni reactiva; el detalle dice el motivo. La respuesta no tiene
    un "modo" único — se explica por la lista `acciones` (puede venir
    vacía si ninguna toma cruzó su propio umbral esta vez). Las que se habrían
    cortado pero se respetan por primacía humana van en
    `respetadas_por_primacia_humana`.

GET .../control-energetico/estado
    Solo consulta: última lectura de batería (con su fecha, aunque sea vieja), modo actual
    (normal/ahorro/supervivencia), qué tomas están apagadas por el sistema en
    este momento, con su categoría y prioridad, y cuáles se apagarán en la
    próxima evaluación (sin las que se respetarán por primacía humana). Nunca ejecuta ninguna acción. Ver
    schemas.EstadoControlEnergeticoOut para el cálculo del modo.

La lógica de /evaluar vive en servicios/control_energetico.py
(evaluar_control_energetico), para que mqtt_listener.py pueda ejecutarla
también automáticamente al recibir una lectura de batería (R13_AUTO).
Este router valida el acceso del usuario y traduce sus errores a HTTP.

Acceso: dueño propietario del domo, o administrador con asignación
activa — mismo criterio de _domos_accesibles() de routers/domos.py
(reutilizado, no reimplementado), igual que en el resto de routers de
lecturas y dispositivos.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Domo, Usuario
from routers.domos import DOMO_NO_ENCONTRADO, _domos_accesibles
from schemas import EstadoControlEnergeticoOut, EvaluarControlEnergeticoOut, TomaApagadaPorSistemaOut, TomaPorCortarOut
from security import get_current_user
from servicios import control_energetico as servicio_r13
from servicios.control_energetico import (
    EvaluacionNoPosible,
    _orden_apagado,
    _tomas_apagadas_por_sistema,
    _ultima_lectura_bateria,
    modo_actual_de,
    prioridad_de,
    tomas_por_cortar,
)
from servicios import mediciones

router = APIRouter(
    prefix="/domos/{domo_id}/control-energetico",
    tags=["Control energético"],
)


def _domo_accesible(db: Session, domo_id: int, usuario: Usuario) -> Domo:
    """Domo asegurando que el usuario autenticado tenga acceso a él (dueño
    propietario o administrador con asignación activa) — reutiliza
    _domos_accesibles() de routers/domos.py en vez de repetir el criterio."""
    domo = _domos_accesibles(db, usuario).filter(Domo.id == domo_id).first()
    if domo is None:
        raise DOMO_NO_ENCONTRADO
    return domo


@router.post("/evaluar", response_model=EvaluarControlEnergeticoOut)
def evaluar_control_energetico(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)
    try:
        return servicio_r13.evaluar_control_energetico(db, domo.id)
    except EvaluacionNoPosible as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/estado", response_model=EstadoControlEnergeticoOut)
def obtener_estado_control_energetico(
    domo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = _domo_accesible(db, domo_id, usuario_actual)

    ultima_bateria = _ultima_lectura_bateria(db, domo.id)
    candidatas = _tomas_apagadas_por_sistema(db, domo)

    tomas_salida = [
        TomaApagadaPorSistemaOut(
            dispositivo_id=toma.id,
            nombre=toma.nombre,
            categoria=toma.categoria,
            prioridad=prioridad_de(toma),
            apagada_desde=accion.fecha_hora,
        )
        for toma, accion in sorted(candidatas, key=lambda par: _orden_apagado(par[0]))
    ]

    # Lo que está por cortarse solo se anuncia con un SoC actual: con uno viejo R-13 no decide.
    reciente = mediciones.ultimo_valor(db, domo.id, "soc_pct", mediciones.vigente_desde())
    soc = reciente[0] if reciente is not None else None

    return EstadoControlEnergeticoOut(
        domo_id=domo.id,
        ultima_lectura_bateria=mediciones.como_lectura(ultima_bateria, "bateria") if ultima_bateria is not None else None,
        modo_actual=modo_actual_de([toma for toma, _ in candidatas]),
        tomas_apagadas_por_sistema=tomas_salida,
        tomas_por_cortar=[
            TomaPorCortarOut(dispositivo_id=toma.id, nombre=toma.nombre, categoria=toma.categoria, umbral_apagado_soc=umbral)
            for toma, umbral in tomas_por_cortar(db, domo, soc)
        ],
    )
