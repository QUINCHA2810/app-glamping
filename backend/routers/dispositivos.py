"""
Endpoints de dispositivos — requisitos R-10 (Control de iluminación),
R-11 (Control de tomas inteligentes) y R-12 (Visualización del estado de
dispositivos). Los tres comparten la tabla `dispositivos`; se diferencian
por el campo `tipo` ('bombillo' vs 'toma'), así que se implementan juntos
en este router.

POST .../dispositivos
    Crea un dispositivo (bombillo o toma) en un domo. Solo el dueño del domo
    puede hacerlo. `conectado` queda en false y `ultima_comunicacion` en null.
    `id_fisico` es opcional y único dentro del domo: sin él, el dispositivo es
    simulado (ver models.py). La categoría (por defecto 'confort' en tomas,
    siempre 'esencial' en bombillos) define prioridad y umbrales, que en v2 se
    CALCULAN con la batería y el perfil del domo y no se guardan (ver
    servicios/categorias.py); la respuesta los trae calculados.

GET .../dispositivos
    Lista los dispositivos activos de ese domo (R-12), por id. Con
    ?incluir_inactivos=true incluye también los desactivados.

GET .../dispositivos/{dispositivo_id}
    Devuelve un dispositivo puntual de ese domo, activo o no.

PATCH .../dispositivos/{dispositivo_id}
    Cambia `estado` (encendido/apagado), si el dispositivo es 'bombillo'
    `brillo`/`color`/`temperatura_color` — el control real de R-10/R-11 — y
    los datos que administra el dueño (CAMPOS_DEL_DUENO): `nombre`,
    `ubicacion`, `id_fisico`, `activo` y `categoria`.

    Modelo v2: los umbrales ya no se editan (no hay modo avanzado). Un cuerpo con
    `umbral_apagado_soc` o `umbral_reactivacion_soc` da 422 con el motivo.

    `activo`=false desactiva sin borrar (un dispositivo nunca se borra: la base
    lo impide si tiene acciones): R-13 lo ignora y el listado lo oculta, pero la
    toma se queda en el estado físico en que esté. `desactivado_en` guarda
    cuándo, y reactivarlo lo vacía.

    Efecto sobre el hardware real, solo en dispositivos con `id_fisico` no nulo
    (ver backend/mqtt_publisher.py y firmware/domo_nodo/):
    - Toma: si el body trae `estado`, se publica la orden en
      `domo/{domo_id}/comando/toma` con origen="manual".
    - Bombillo: se publica en `domo/{domo_id}/comando/bombillo` con
      origen="manual" (R-13 nunca publica aquí: no toca bombillos):
        * apagarlo: {"accion": "off"}, sin luz;
        * encenderlo: {"accion": "on"} más la luz guardada en su modo;
        * cambiar la luz (`brillo`, `color`, `temperatura_color`, `modo_luz`,
          `escena`) con el bombillo encendido: la luz guardada en su modo, completa
          (en color, el brillo va con el color, para no perderlo);
        * cambiar la luz con el bombillo APAGADO: solo se guarda. Se aplica al
          encenderlo (el frontend manda estado "encendido" junto con el modo).
      Sin `modo_luz` guardado, la orden es la de antes: accion y, si cambió, brillo.
      La orden de cada modo la arma servicios/iluminacion.orden_de_luz.

    Luz: sin `modo_luz` en el cuerpo, el campo de luz que venga lo fija
    (temperatura_color → blanco, color → color, escena → escena). El modo escena
    necesita una escena (en el cuerpo o ya guardada).

    El orden es siempre el mismo: primero el db.commit(), después la
    publicación. Si el broker está caído, la publicación falla en silencio
    (queda en el log) y la respuesta HTTP es la misma: el PATCH ya se guardó.

    Cuando el body incluye `estado`, además registra la acción en `acciones`
    (actor_tipo = el rol del usuario EN ESTE DOMO, actor_ref = su correo,
    condicion='control manual'). Esto no es solo para R-16: R-13 decide si
    puede reactivar una toma mirando si su ÚLTIMA acción fue un apagado del
    sistema — si un cambio manual no quedara registrado aquí, el algoritmo
    podría reactivar una toma que alguien apagó a propósito. Y al revés: si la
    última acción es un encendido manual, R-13 la respeta (primacía humana, DB-4)
    hasta que la batería se recupere o baje de la reserva; las respuestas lo
    indican en `primacia_humana_hasta_soc`.

Acceso:
- POST es exclusivo del dueño de ESE domo (routers/domos.py, domo_del_dueno).
- GET (lista y puntual) y PATCH aceptan al dueño o a un administrador del domo.
  En el PATCH, quien es administrador de ESE domo recibe 403 si el cuerpo trae
  algún campo de CAMPOS_DEL_DUENO: él opera (encender, apagar, iluminación), no
  administra. El rol se mira en el domo, no en la cuenta: un usuario puede ser
  dueño de un domo y administrador de otro.
- Todas las búsquedas de dispositivo van acotadas por `domo_id`. 404 en vez de
  403 en cualquier caso sin acceso, para no revelar que el domo o el dispositivo
  existen.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import ROL_DUENO, Accion, Dispositivo, Usuario
from mqtt_publisher import publicar_comando_bombillo, publicar_comando_toma
from routers.domos import domo_accesible, domo_del_dueno, rol_en_domo
from schemas import UMBRALES_CALCULADOS, DispositivoCreate, DispositivoOut, DispositivoUpdate, modo_de_los_campos
from security import get_current_user, require_dueno
from servicios.categorias import CONFORT, ESENCIAL, ajustes_del_domo, preset_de
from servicios.comunicacion import confirmacion_de, ultima_orden
from servicios.control_energetico import PrimaciaHumana, _ultimas_acciones, primacias_humanas
from servicios.iluminacion import orden_de_luz

router = APIRouter(prefix="/domos/{domo_id}/dispositivos", tags=["Dispositivos"])

DISPOSITIVO_NO_ENCONTRADO = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Dispositivo no encontrado.",
)

CAMPOS_DE_UMBRAL = {"umbral_apagado_soc", "umbral_reactivacion_soc"}
CAMPOS_DEL_DUENO = {"nombre", "ubicacion", "id_fisico", "activo", "categoria"}
CAMPOS_DE_LUZ = {"brillo", "color", "temperatura_color", "modo_luz", "escena"}

# routers/estado_comunicacion.py lo importa desde aquí.
_domo_accesible = domo_accesible


def _validar_id_fisico_libre(db: Session, domo_id: int, id_fisico: str, excepto_id: Optional[int] = None) -> None:
    consulta = db.query(Dispositivo).filter(Dispositivo.domo_id == domo_id, Dispositivo.id_fisico == id_fisico)
    if excepto_id is not None:
        consulta = consulta.filter(Dispositivo.id != excepto_id)
    if consulta.first() is not None:
        raise HTTPException(status_code=409, detail="Ya hay otro dispositivo de este domo con ese id_fisico.")


def _guardar(db: Session) -> None:
    """Commit que traduce una restricción violada a 422 en vez de un 500. Las
    validaciones ya las cubren: queda para carreras entre peticiones."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=422, detail="Los datos no cumplen las restricciones del dispositivo.")


def _salida(
    dispositivo: Dispositivo,
    ajustes: tuple[str, str],
    primacias: Optional[dict[int, PrimaciaHumana]] = None,
    orden: Optional[datetime] = None,
) -> DispositivoOut:
    """Con la prioridad, los umbrales, la primacía humana y la confirmación de la última
    orden (`orden`: su fecha) calculados (no se guardan)."""
    preset = preset_de(dispositivo.categoria, *ajustes)
    primacia = (primacias or {}).get(dispositivo.id)
    return DispositivoOut.model_validate(dispositivo).model_copy(
        update={
            "prioridad": preset.prioridad,
            "umbral_apagado_soc": preset.umbral_apagado_soc,
            "umbral_reactivacion_soc": preset.umbral_reactivacion_soc,
            "personalizado": False,
            "primacia_humana_hasta_soc": primacia.hasta_soc if primacia else None,
            "primacia_humana_desde": primacia.desde if primacia else None,
            "confirmacion": confirmacion_de(dispositivo, orden),
        }
    )


def _comando_bombillo(dispositivo: Dispositivo, cambios: dict) -> Optional[dict]:
    """La orden MQTT para un bombillo real tras aplicar `cambios` (ver el docstring del
    módulo), o None si no hay nada que publicar. `dispositivo` ya tiene los cambios."""
    luz = orden_de_luz(
        dispositivo.modo_luz, dispositivo.brillo, dispositivo.color, dispositivo.temperatura_color, dispositivo.escena
    )
    if dispositivo.modo_luz is None:
        # Sin modo, como antes: el brillo solo si cambió.
        luz = {"brillo": cambios["brillo"]} if cambios.get("brillo") is not None else {}
    if cambios.get("estado") == "apagado":
        orden = {"accion": "off"}
    elif cambios.get("estado") == "encendido":
        orden = {"accion": "on", **luz}
    elif CAMPOS_DE_LUZ & cambios.keys() and dispositivo.estado == "encendido":
        orden = luz
    else:
        return None
    return {"id_fisico": dispositivo.id_fisico, **orden} if orden else None


def _dispositivo_del_domo(db: Session, domo_id: int, dispositivo_id: int) -> Dispositivo:
    """Dispositivo acotado siempre por domo_id, para no exponer uno de
    otro domo a quien adivine su id."""
    dispositivo = (
        db.query(Dispositivo)
        .filter(Dispositivo.id == dispositivo_id, Dispositivo.domo_id == domo_id)
        .first()
    )
    if dispositivo is None:
        raise DISPOSITIVO_NO_ENCONTRADO
    return dispositivo


@router.post("", response_model=DispositivoOut, status_code=status.HTTP_201_CREATED)
def crear_dispositivo(
    domo_id: int,
    datos: DispositivoCreate,
    db: Session = Depends(get_db),
    dueno: Usuario = Depends(require_dueno),
):
    domo = domo_del_dueno(db, domo_id, dueno)
    if datos.id_fisico is not None:
        _validar_id_fisico_libre(db, domo.id, datos.id_fisico)

    nuevo_dispositivo = Dispositivo(
        domo_id=domo.id,
        tipo=datos.tipo,
        nombre=datos.nombre,
        ubicacion=datos.ubicacion,
        categoria=datos.categoria or (CONFORT if datos.tipo == "toma" else ESENCIAL),
        id_fisico=datos.id_fisico,
        brillo=datos.brillo,
        color=datos.color,
        temperatura_color=datos.temperatura_color,
        # estado usa su default ("apagado"); conectado, false.
    )
    db.add(nuevo_dispositivo)
    _guardar(db)
    db.refresh(nuevo_dispositivo)
    return _salida(nuevo_dispositivo, ajustes_del_domo(db, domo.id))


@router.get("", response_model=list[DispositivoOut])
def listar_dispositivos(
    domo_id: int,
    incluir_inactivos: bool = False,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    consulta = db.query(Dispositivo).filter(Dispositivo.domo_id == domo.id)
    if not incluir_inactivos:
        consulta = consulta.filter(Dispositivo.activo.is_(True))
    ajustes = ajustes_del_domo(db, domo.id)
    # La última acción de cada toma activa, en una consulta: para la primacía y la confirmación.
    ultimas = _ultimas_acciones(db, domo)
    primacias = primacias_humanas(db, domo, ultimas=ultimas)
    return [
        _salida(dispositivo, ajustes, primacias, ultimas[dispositivo.id].fecha_hora if dispositivo.id in ultimas else None)
        for dispositivo in consulta.order_by(Dispositivo.id).all()
    ]


@router.get("/{dispositivo_id}", response_model=DispositivoOut)
def obtener_dispositivo(
    domo_id: int,
    dispositivo_id: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    dispositivo = _dispositivo_del_domo(db, domo.id, dispositivo_id)
    return _salida(dispositivo, ajustes_del_domo(db, domo.id), primacias_humanas(db, domo), ultima_orden(db, dispositivo.id))


@router.patch("/{dispositivo_id}", response_model=DispositivoOut)
def actualizar_dispositivo(
    domo_id: int,
    dispositivo_id: int,
    datos: DispositivoUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    domo = domo_accesible(db, domo_id, usuario_actual)
    dispositivo = _dispositivo_del_domo(db, domo.id, dispositivo_id)
    rol = rol_en_domo(db, usuario_actual, domo.id)

    cambios = datos.model_dump(exclude_unset=True)

    # Los umbrales cuentan como del dueño: un administrador recibe 403, como en v1,
    # aunque el dueño reciba 422 porque ya no se editan.
    if rol != ROL_DUENO and (CAMPOS_DEL_DUENO | CAMPOS_DE_UMBRAL) & cambios.keys():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El administrador solo puede operar el dispositivo; el resto de sus datos los cambia el dueño.",
        )
    # 422 literal en los chequeos: el nombre de la constante cambió entre
    # versiones de Starlette.
    if CAMPOS_DE_UMBRAL & cambios.keys():
        raise HTTPException(status_code=422, detail=UMBRALES_CALCULADOS)
    if dispositivo.tipo != "bombillo" and CAMPOS_DE_LUZ & cambios.keys():
        raise HTTPException(
            status_code=422,
            detail="brillo, color, temperatura_color, modo_luz y escena solo aplican a dispositivos tipo 'bombillo'.",
        )
    # El campo de luz que venga fija el modo, si el cuerpo no lo trae (el esquema ya
    # rechazó las mezclas y las contradicciones).
    if cambios.get("modo_luz") is None:
        modo = modo_de_los_campos(cambios)
        if modo is not None:
            cambios["modo_luz"] = modo
    escena_final = cambios["escena"] if "escena" in cambios else dispositivo.escena
    modo_final = cambios["modo_luz"] if "modo_luz" in cambios else dispositivo.modo_luz
    if modo_final == "escena" and escena_final is None:
        raise HTTPException(status_code=422, detail="El modo escena necesita una escena.")
    if dispositivo.tipo != "toma" and cambios.get("categoria", ESENCIAL) != ESENCIAL:
        raise HTTPException(status_code=422, detail="Un bombillo siempre es de categoría 'esencial'.")

    if cambios.get("id_fisico") is not None:
        _validar_id_fisico_libre(db, domo.id, cambios["id_fisico"], excepto_id=dispositivo.id)

    estado_anterior = dispositivo.estado
    if "activo" in cambios and cambios["activo"] != dispositivo.activo:
        dispositivo.desactivado_en = None if cambios["activo"] else datetime.now(timezone.utc)
    for campo, valor in cambios.items():
        setattr(dispositivo, campo, valor)

    # Órdenes para el hardware real: se deciden ANTES del commit (después la sesión
    # expira el objeto) y se publican DESPUÉS. Solo con id_fisico no nulo.
    comando_mqtt: Optional[tuple[str, str]] = None
    if "estado" in cambios and dispositivo.tipo == "toma" and dispositivo.id_fisico is not None:
        comando_mqtt = (dispositivo.id_fisico, "on" if cambios["estado"] == "encendido" else "off")
    comando_bombillo: Optional[dict] = None
    if dispositivo.tipo == "bombillo" and dispositivo.id_fisico is not None:
        comando_bombillo = _comando_bombillo(dispositivo, cambios)

    # Acción (R-16) solo cuando cambia `estado`: es la señal manual que R-13
    # necesita para no reactivar una toma que un humano acaba de apagar.
    if "estado" in cambios:
        db.add(
            Accion(
                dispositivo_id=dispositivo.id,
                accion="encender" if cambios["estado"] == "encendido" else "apagar",
                estado_anterior=estado_anterior,
                estado_nuevo=cambios["estado"],
                actor_tipo=rol,
                actor_ref=usuario_actual.correo,
                condicion="control manual",
                resultado="ok",
            )
        )

    _guardar(db)

    # Recién ahora, con el estado ya persistido, se propaga la orden. Si el broker
    # está caído, se loguea y la respuesta no cambia.
    if comando_mqtt is not None:
        id_fisico, accion_mqtt = comando_mqtt
        publicar_comando_toma(domo_id=domo_id, accion=accion_mqtt, id_fisico=id_fisico, origen="manual")
    if comando_bombillo is not None:
        publicar_comando_bombillo(domo_id=domo_id, origen="manual", **comando_bombillo)

    db.refresh(dispositivo)
    return _salida(
        dispositivo, ajustes_del_domo(db, domo_id), primacias_humanas(db, domo), ultima_orden(db, dispositivo.id)
    )
