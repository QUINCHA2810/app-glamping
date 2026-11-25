"""
Lógica del algoritmo de supervivencia — requisito R-13 — sin nada de HTTP,
para poder ejecutarla desde dos lugares:

- el endpoint POST /domos/{domo_id}/control-energetico/evaluar
  (routers/control_energetico.py), que valida el acceso del usuario y
  traduce los errores de este módulo a HTTP 409;
- mqtt_listener.py, que la dispara automáticamente al recibir una lectura
  de batería (R13_AUTO, ver README).

Modelo v2: los umbrales y la prioridad de cada toma se CALCULAN en cada
evaluación con su categoría y la batería y el perfil del domo
(servicios/categorias.py). No hay umbrales guardados, propios ni globales.

Garantías que cualquier llamador hereda:
- Solo se decide con un SoC ACTUAL: el último SoC con valor del domo, si no tiene más
  de mediciones.VIGENCIA_S (120 s) y la lectura de batería más reciente lo trae (como
  en v1). Si es más viejo, o no hay ninguno, no se corta ni
  se reactiva nada: EvaluacionNoPosible con el motivo (DB-3; antes, un ESP32 sin
  sensor que publica el SoC en null hacía que R-13 usara un SoC de días atrás).
- Solo se tocan dispositivos tipo='toma' y activos; bombillos e inactivos nunca.
- Nunca se corta una toma de categoría 'esencial'. El apagado manual sigue
  permitido, porque no pasa por aquí.
- Solo se reactivan tomas cuya ÚLTIMA acción fue un apagado del sistema
  (actor_tipo='sistema'). Si una de ellas ahora es esencial (la cortaron antes
  de cambiarle la categoría), se reactiva sin mirar el SoC.
- El orden es determinista: a igual prioridad, por id.
- La publicación MQTT ocurre DESPUÉS del db.commit(), solo para tomas con
  id_fisico no nulo y nunca retenida (retain=False, ver mqtt_publisher.py).

Primacía de la decisión humana (decisión de diseño):
- Si la ÚLTIMA acción de una toma es un ENCENDIDO de una persona (actor_tipo
  'dueno' o 'administrador'), R-13 no la corta por su umbral de apagado mientras
  la primacía esté vigente: hasta que, DESPUÉS de esa acción, haya una medición con
  SoC >= el umbral de reactivación de la toma (la batería "se recuperó"). Desde ese
  momento vuelve a la regla normal. Nada se guarda: sale de `acciones` y
  `mediciones` (primacias_humanas).
- Excepción de seguridad: con el SoC actual BAJO la reserva de la batería del
  domo (reserva_de), R-13 la corta igual. Esa acción guarda "motivo=reserva" en
  acciones.condicion y sale con motivo='reserva' en la respuesta de /evaluar.
- Apagar a mano sigue igual: R-13 nunca reactiva una toma que apagó una persona.

Alertas automáticas (R-15) — en la MISMA transacción que las acciones,
antes del commit:
- Si la evaluación apaga al menos una toma confort o importante (cortar solo
  prescindibles no alerta: es el estado "ahorro"), se crea UNA alerta
  tipo='bateria_baja', variable 'estado_carga_soc', valor detectado = SoC,
  límite = umbral de la primera de esas tomas, nivel 'critico' si
  SoC <= reserva de la batería del domo y si no 'advertencia', mensaje
  "R-13 cortó cargas: <tomas>" y creada_por_r13 = true. No se duplica: si el
  domo ya tiene una alerta 'bateria_baja' activa (de R-13 o creada a mano), no
  se crea otra.
- Si la evaluación reactiva tomas y no corta ninguna confort o importante, y
  además ya no queda ninguna de esas dos categorías apagada por el sistema, las
  alertas activas que creó R-13 (creada_por_r13) pasan a 'atendida'. Una
  'bateria_baja' creada a mano queda como está.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Collection, Optional, Sequence

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from models import (
    ACTOR_REF_R13,
    ACTOR_SISTEMA,
    ROL_ADMINISTRADOR,
    ROL_DUENO,
    Accion,
    Alerta,
    Dispositivo,
    Domo,
    Medicion,
)
from mqtt_publisher import publicar_comando_toma
from schemas import AccionControlEnergeticoOut, EvaluarControlEnergeticoOut, TomaRespetadaOut
from servicios import mediciones
from servicios.reportes import ZONA_BOGOTA
from servicios.categorias import (
    CONFORT,
    ESENCIAL,
    IMPORTANTE,
    PRESCINDIBLE,
    PRIORIDAD_POR_CATEGORIA,
    RESERVA_SOC_POR_BATERIA,
    perfil_de,
    reserva_de,
    tipo_bateria_de,
    umbrales_de,
    umbrales_globales_de,
)

# Alertas automáticas de R-13 (ver docstring del módulo).
TIPO_ALERTA_R13 = "bateria_baja"
VARIABLE_ALERTA_R13 = "estado_carga_soc"
PREFIJO_ALERTA_R13 = "R-13 cortó cargas: "
CATEGORIAS_CON_ALERTA = (CONFORT, IMPORTANTE)


class EvaluacionNoPosible(Exception):
    """No se puede evaluar R-13 para el domo con los datos actuales (no hay un SoC
    de los últimos mediciones.VIGENCIA_S segundos). El endpoint la traduce a 409 con
    este mismo mensaje; el listener la registra una vez por motivo."""


def soc_actual(db: Session, domo_id: int, ahora: Optional[datetime] = None) -> float:
    """El SoC con el que R-13 puede decidir: el último con valor, si es actual (ver
    mediciones.VIGENCIA_S). Si no, EvaluacionNoPosible con el motivo."""
    reciente = mediciones.ultimo_valor(db, domo_id, "soc_pct", mediciones.vigente_desde(ahora))
    if reciente is not None:
        # Como en v1: si la lectura de batería más reciente llegó sin SoC, tampoco se decide.
        ultima_bateria = _ultima_lectura_bateria(db, domo_id)
        if ultima_bateria.soc_pct is None:
            raise EvaluacionNoPosible("La lectura de batería más reciente no tiene estado_carga_soc registrado.")
        return reciente[0]
    anterior = mediciones.ultimo_valor(db, domo_id, "soc_pct")
    minutos = mediciones.VIGENCIA_S // 60
    if anterior is None:
        raise EvaluacionNoPosible(
            "Este domo no tiene ningún estado de carga (SoC) registrado; no se puede evaluar el control energético."
        )
    valor, fecha = anterior
    fecha_local = fecha.astimezone(ZONA_BOGOTA).strftime("%Y-%m-%d %H:%M")
    raise EvaluacionNoPosible(
        f"No hay estado de carga (SoC) de los últimos {minutos} minutos: el último registrado es {valor:g} % "
        f"del {fecha_local} (hora de Colombia). Con datos viejos el control energético no corta ni reactiva nada."
    )


def _ultima_lectura_bateria(db: Session, domo_id: int) -> Optional[Medicion]:
    """La medición más reciente con algún dato de batería."""
    return mediciones.ultima(db, domo_id, "bateria")


def _ultima_accion_de_dispositivo(db: Session, dispositivo_id: int) -> Optional[Accion]:
    return (
        db.query(Accion)
        .filter(Accion.dispositivo_id == dispositivo_id)
        .order_by(Accion.fecha_hora.desc(), Accion.id.desc())
        .first()
    )


def _tomas_activas(db: Session, domo: Domo) -> list[Dispositivo]:
    return (
        db.query(Dispositivo)
        .filter(Dispositivo.domo_id == domo.id, Dispositivo.tipo == "toma", Dispositivo.activo.is_(True))
        .order_by(Dispositivo.id)
        .all()
    )


def _ultimas_acciones(db: Session, domo: Domo) -> dict[int, Accion]:
    """{dispositivo_id: su ÚLTIMA acción} de las tomas activas del domo, en UNA
    consulta (mismo orden que _ultima_accion_de_dispositivo: fecha y luego id). Parte
    de las tomas: la subconsulta corre una vez por toma, con el índice
    acciones_dispositivo_fecha_idx (dispositivo_id, fecha_hora DESC, id DESC), sin
    recorrer el historial."""
    otra = aliased(Accion)
    ultima_id = (
        select(otra.id)
        .where(otra.dispositivo_id == Dispositivo.id)
        .order_by(otra.fecha_hora.desc(), otra.id.desc())
        .limit(1)
        .correlate(Dispositivo)
        .scalar_subquery()
    )
    filas = (
        db.query(Accion)
        .select_from(Dispositivo)
        .join(Accion, Accion.id == ultima_id)
        .filter(
            Dispositivo.domo_id == domo.id,
            Dispositivo.tipo == "toma",
            Dispositivo.activo.is_(True),
        )
        .all()
    )
    return {accion.dispositivo_id: accion for accion in filas}


def _apagada_por_sistema(accion: Optional[Accion]) -> bool:
    return accion is not None and accion.accion == "apagar" and accion.actor_tipo == ACTOR_SISTEMA


def _tomas_apagadas_por_sistema(
    db: Session,
    domo: Domo,
    tomas: Optional[list[Dispositivo]] = None,
    ultimas: Optional[dict[int, Accion]] = None,
) -> list[tuple[Dispositivo, Accion]]:
    """Tomas activas cuya ÚLTIMA acción es un apagado del sistema — nadie las
    ha tocado manualmente desde entonces (un PATCH manual deja su propia
    acción). Son las únicas candidatas a reactivación automática, y también lo
    que GET .../estado reporta como "apagadas por el sistema".

    `tomas` y `ultimas` son para no repetir las consultas si quien llama ya las tiene."""
    tomas = _tomas_activas(db, domo) if tomas is None else tomas
    ultimas = _ultimas_acciones(db, domo) if ultimas is None else ultimas
    return [(toma, ultimas[toma.id]) for toma in tomas if _apagada_por_sistema(ultimas.get(toma.id))]


ACTORES_HUMANOS = (ROL_DUENO, ROL_ADMINISTRADOR)
MOTIVO_RESERVA = "reserva"


@dataclass(frozen=True)
class PrimaciaHumana:
    """Una toma encendida a mano que R-13 respeta (ver el docstring del módulo)."""

    # Cuándo la encendió la persona (la acción que da la primacía).
    desde: datetime
    # El umbral de reactivación: cuando, después de `desde`, el SoC llegue aquí, se acaba.
    hasta_soc: float


def primacias_humanas(
    db: Session,
    domo: Domo,
    tomas: Optional[list[Dispositivo]] = None,
    ultimas: Optional[dict[int, Accion]] = None,
) -> dict[int, PrimaciaHumana]:
    """{toma_id: PrimaciaHumana} de las tomas con primacía humana VIGENTE: activas,
    encendidas, no esenciales, cuya última acción es un encendido de una persona, y
    sin ninguna medición del domo con SoC >= su umbral de reactivación DESPUÉS de esa
    acción.

    Costo: si ninguna toma tiene un encendido humano como última acción (lo normal),
    no consulta nada más. Si alguna lo tiene, UNA sola consulta para todo el domo,
    con un EXISTS por toma sobre el índice (domo_id, fecha_hora) de mediciones; cada
    EXISTS para en la primera fila que cumple."""
    tomas = _tomas_activas(db, domo) if tomas is None else tomas
    ultimas = _ultimas_acciones(db, domo) if ultimas is None else ultimas
    tipo, perfil = tipo_bateria_de(domo), perfil_de(domo)

    candidatas: list[tuple[int, datetime, float]] = []
    for toma in tomas:
        accion = ultimas.get(toma.id)
        if (
            toma.estado != "encendido"
            or toma.categoria == ESENCIAL
            or accion is None
            or accion.accion != "encender"
            or accion.actor_tipo not in ACTORES_HUMANOS
        ):
            continue
        reactivacion = umbrales_de(toma.categoria, tipo, perfil)[1]
        candidatas.append((toma.id, mediciones.a_utc(accion.fecha_hora), reactivacion))
    if not candidatas:
        return {}

    recuperadas = db.execute(
        select(
            *(
                exists()
                .where(
                    Medicion.domo_id == domo.id,
                    Medicion.fecha_hora > desde,
                    Medicion.soc_pct >= reactivacion,
                )
                .label(f"t{indice}")
                for indice, (_, desde, reactivacion) in enumerate(candidatas)
            )
        )
    ).one()
    return {
        toma_id: PrimaciaHumana(desde=desde, hasta_soc=reactivacion)
        for (toma_id, desde, reactivacion), recuperada in zip(candidatas, recuperadas)
        if not recuperada
    }


def _tomas_que_se_pueden_cortar(db: Session, domo: Domo) -> list[Dispositivo]:
    """Tomas encendidas y activas que no son esenciales."""
    return (
        db.query(Dispositivo)
        .filter(
            Dispositivo.domo_id == domo.id,
            Dispositivo.tipo == "toma",
            Dispositivo.estado == "encendido",
            Dispositivo.activo.is_(True),
            Dispositivo.categoria != ESENCIAL,
        )
        .all()
    )


def prioridad_de(dispositivo) -> int:
    return PRIORIDAD_POR_CATEGORIA[dispositivo.categoria]


def _orden_apagado(dispositivo) -> tuple[int, int]:
    """Prioridad ascendente (más baja = menos esencial = se apaga primero);
    a igual prioridad, por id."""
    return (prioridad_de(dispositivo), dispositivo.id)


def _orden_reactivacion(dispositivo) -> tuple[int, int]:
    """Prioridad descendente (más alta = más esencial = se reactiva primero);
    a igual prioridad, por id."""
    return (-prioridad_de(dispositivo), dispositivo.id)


def modo_actual_de(tomas_cortadas: list) -> str:
    """'normal', 'ahorro' o 'supervivencia' según lo que R-13 YA apagó — ver
    schemas.EstadoControlEnergeticoOut."""
    if not tomas_cortadas:
        return "normal"
    return "ahorro" if all(toma.categoria == PRESCINDIBLE for toma in tomas_cortadas) else "supervivencia"


def tomas_por_cortar(db: Session, domo: Domo, soc: Optional[float]) -> list[tuple[Dispositivo, float]]:
    """(toma, umbral) de las encendidas que con este SoC se apagarán en la próxima
    evaluación, en el orden en que se cortarían. Usa decidir_r13, así que no anuncia
    las que R-13 respetará por primacía humana."""
    if soc is None:
        return []
    tipo, perfil = tipo_bateria_de(domo), perfil_de(domo)
    tomas = _tomas_activas(db, domo)
    cortables = [toma for toma in tomas if toma.estado == "encendido" and toma.categoria != ESENCIAL]
    if not cortables:
        return []
    primacias = primacias_humanas(db, domo, tomas)
    decision = decidir_r13(soc, cortables, [], tipo, perfil, primacias)
    return [(toma, umbrales_de(toma.categoria, tipo, perfil)[0]) for toma in decision.a_apagar]


@dataclass(frozen=True)
class DecisionR13:
    """Qué hace una evaluación de R-13, sin haber tocado nada todavía."""

    # En el orden en que se ejecutan.
    a_apagar: list
    a_reactivar: list
    # Hay que crear la alerta de corte (salvo que ya haya una activa), con este umbral.
    crear_alerta: bool
    umbral_alerta: Optional[float]
    # Hay que pasar a 'atendida' las alertas de corte activas.
    atender_alertas: bool
    # Primacía humana: las que habrían cruzado su umbral de apagado pero se respetan,
    # en orden de apagado, y los id de las de `a_apagar` que se cortan por la reserva
    # aunque las haya encendido una persona.
    respetadas: list = field(default_factory=list)
    cortadas_por_reserva: frozenset = frozenset()


def decidir_r13(
    soc: float,
    encendidas_cortables: Sequence,
    apagadas_por_sistema: Sequence,
    tipo_bateria: str,
    perfil: str,
    con_primacia_humana: Collection[int] = (),
) -> DecisionR13:
    """Las reglas de R-13, sin base de datos: la usan evaluar_control_energetico y
    tools/generar_datos_demo.py, que la aplica con un reloj simulado. De cada toma
    solo lee `id` y `categoria`.

    `encendidas_cortables` son las tomas encendidas, activas y no esenciales, y
    `apagadas_por_sistema` las tomas activas cuya última acción fue un apagado del
    sistema; ambas tal como estaban ANTES de esta evaluación. `con_primacia_humana`
    son los id de las tomas con primacía humana vigente (primacias_humanas): no se
    cortan por su umbral, solo si el SoC está bajo la reserva de la batería."""

    def apagado(toma) -> float:
        return umbrales_de(toma.categoria, tipo_bateria, perfil)[0]

    def reactivacion(toma) -> float:
        return umbrales_de(toma.categoria, tipo_bateria, perfil)[1]

    reserva = RESERVA_SOC_POR_BATERIA[tipo_bateria]
    bajo_umbral = sorted((toma for toma in encendidas_cortables if soc < apagado(toma)), key=_orden_apagado)
    respetadas = [toma for toma in bajo_umbral if toma.id in con_primacia_humana and soc >= reserva]
    ids_respetadas = {toma.id for toma in respetadas}
    a_apagar = [toma for toma in bajo_umbral if toma.id not in ids_respetadas]
    cortadas_por_reserva = frozenset(toma.id for toma in a_apagar if toma.id in con_primacia_humana)
    a_reactivar = sorted(
        (toma for toma in apagadas_por_sistema if toma.categoria == ESENCIAL or soc >= reactivacion(toma)),
        key=_orden_reactivacion,
    )
    # Prescindibles cortadas no alertan ni mantienen la alerta (ver el docstring del módulo).
    cortes_con_alerta = [toma for toma in a_apagar if toma.categoria in CATEGORIAS_CON_ALERTA]
    siguen_cortadas = [toma for toma in apagadas_por_sistema if toma not in a_reactivar]
    atender = (
        not cortes_con_alerta
        and bool(a_reactivar)
        and not any(toma.categoria in CATEGORIAS_CON_ALERTA for toma in siguen_cortadas)
    )
    return DecisionR13(
        a_apagar=a_apagar,
        a_reactivar=a_reactivar,
        crear_alerta=bool(cortes_con_alerta),
        umbral_alerta=apagado(cortes_con_alerta[0]) if cortes_con_alerta else None,
        atender_alertas=atender,
        respetadas=respetadas,
        cortadas_por_reserva=cortadas_por_reserva,
    )


def nivel_alerta_corte(soc: float, reserva: float) -> str:
    return "critico" if soc <= reserva else "advertencia"


def condicion_control_sistema(soc: float, motivo: Optional[str] = None) -> str:
    """Lo que R-13 guarda en acciones.condicion. Con motivo 'reserva': la toma la
    había encendido una persona y se cortó porque el SoC bajó de la reserva."""
    condicion = f"modo=supervivencia; soc={soc}"
    return f"{condicion}; motivo={motivo}" if motivo else condicion


def descripcion_alerta_corte(tomas_cortadas: Sequence) -> str:
    return PREFIJO_ALERTA_R13 + ", ".join(toma.nombre for toma in tomas_cortadas)


def _registrar_accion_sistema(
    db: Session,
    toma: Dispositivo,
    accion: str,
    estado_anterior: str,
    estado_nuevo: str,
    soc: float,
    motivo: Optional[str] = None,
) -> Accion:
    fila = Accion(
        dispositivo_id=toma.id,
        accion=accion,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        actor_tipo=ACTOR_SISTEMA,
        actor_ref=ACTOR_REF_R13,
        condicion=condicion_control_sistema(soc, motivo),
        resultado="ok",
    )
    db.add(fila)
    db.flush()
    db.refresh(fila)
    return fila


def _alertas_bateria_baja_activas(db: Session, domo: Domo) -> list[Alerta]:
    return (
        db.query(Alerta)
        .filter(Alerta.domo_id == domo.id, Alerta.tipo == TIPO_ALERTA_R13, Alerta.estado == "activa")
        .all()
    )


def _registrar_alerta_corte(db: Session, domo: Domo, soc: float, tomas_cortadas: list, umbral_primera: float) -> None:
    """Crea la alerta de corte de cargas, salvo que el domo ya tenga una
    'bateria_baja' activa (no se duplica). Solo hace db.add: el commit es
    el mismo de las acciones."""
    if _alertas_bateria_baja_activas(db, domo):
        return
    db.add(
        Alerta(
            domo_id=domo.id,
            tipo=TIPO_ALERTA_R13,
            mensaje=descripcion_alerta_corte(tomas_cortadas),
            nivel=nivel_alerta_corte(soc, reserva_de(domo)),
            variable=VARIABLE_ALERTA_R13,
            valor_detectado=soc,
            limite=umbral_primera,
            estado="activa",
            creada_por_r13=True,
        )
    )


def _atender_alertas_corte(db: Session, domo: Domo) -> None:
    """Pasa a 'atendida' las alertas activas que creó R-13 en este domo."""
    for alerta in _alertas_bateria_baja_activas(db, domo):
        if alerta.creada_por_r13:
            alerta.cambiar_estado("atendida")


def evaluar_control_energetico(db: Session, domo_id: int, ahora: Optional[datetime] = None) -> EvaluarControlEnergeticoOut:
    """Ejecuta R-13 sobre el domo contra su lectura de batería más
    reciente: guarda los cambios de estado y las acciones (commit) y
    después publica por MQTT las órdenes para las tomas físicas.

    NO valida permisos de usuario — eso es responsabilidad del llamador
    (el endpoint lo hace con _domos_accesibles; el listener es tráfico de
    infraestructura interna sin usuario).

    Lanza LookupError si el domo no existe y EvaluacionNoPosible si no
    hay un SoC actual con qué evaluar (soc_actual). `ahora` es para las pruebas.
    """
    domo = db.get(Domo, domo_id)
    if domo is None:
        raise LookupError(f"El domo {domo_id} no existe.")

    soc = soc_actual(db, domo.id, ahora)

    tipo, perfil = tipo_bateria_de(domo), perfil_de(domo)
    umbral_global_bajo, umbral_global_recuperacion = umbrales_globales_de(tipo, perfil)

    # Se leen ambos grupos ANTES de mutar nada, para que la decisión de
    # "quién se apaga" / "quién se reactiva" se base en el estado previo a
    # esta llamada, no en cambios que la propia llamada vaya haciendo.
    # Consultas: las tomas activas y la última acción de cada una (una por domo), y
    # la de la primacía humana solo si alguna toma la puede tener.
    tomas = _tomas_activas(db, domo)
    ultimas = _ultimas_acciones(db, domo)
    tomas_encendidas = [toma for toma in tomas if toma.estado == "encendido" and toma.categoria != ESENCIAL]
    tomas_apagadas_sistema = [toma for toma, _ in _tomas_apagadas_por_sistema(db, domo, tomas, ultimas)]
    primacias = primacias_humanas(db, domo, tomas, ultimas)
    decision = decidir_r13(soc, tomas_encendidas, tomas_apagadas_sistema, tipo, perfil, primacias)
    reserva = reserva_de(domo)

    acciones_salida: list[AccionControlEnergeticoOut] = []
    # (id_fisico, acción MQTT) de las tomas FÍSICAS afectadas. Se arma ANTES del
    # commit (después la sesión expira los objetos) y se publica DESPUÉS.
    comandos_mqtt: list[tuple[str, str]] = []

    # Se arma antes del commit, por lo mismo.
    respetadas_salida = [
        TomaRespetadaOut(
            dispositivo_id=toma.id,
            nombre=toma.nombre,
            categoria=toma.categoria,
            umbral_apagado_soc=umbrales_de(toma.categoria, tipo, perfil)[0],
            hasta_soc=primacias[toma.id].hasta_soc,
            reserva_soc=reserva,
            encendida_desde=primacias[toma.id].desde,
        )
        for toma in decision.respetadas
    ]

    for toma in decision.a_apagar:
        estado_anterior = toma.estado
        umbral = umbrales_de(toma.categoria, tipo, perfil)[0]
        motivo = MOTIVO_RESERVA if toma.id in decision.cortadas_por_reserva else None
        toma.estado = "apagado"
        if toma.id_fisico is not None:
            comandos_mqtt.append((toma.id_fisico, "off"))
        accion = _registrar_accion_sistema(db, toma, "apagar", estado_anterior, "apagado", soc, motivo)
        acciones_salida.append(
            AccionControlEnergeticoOut(
                dispositivo_id=toma.id,
                nombre=toma.nombre,
                prioridad=prioridad_de(toma),
                accion_realizada="apagar",
                # Cortada por la reserva: el umbral que cruzó es el de la reserva.
                umbral_aplicado=reserva if motivo else umbral,
                motivo=motivo,
                fecha_hora=accion.fecha_hora,
            )
        )

    for toma in decision.a_reactivar:
        estado_anterior = toma.estado
        # Una esencial cortada antes de cambiarle la categoría se reactiva sin umbral.
        umbral = umbrales_de(toma.categoria, tipo, perfil)[1]
        toma.estado = "encendido"
        if toma.id_fisico is not None:
            comandos_mqtt.append((toma.id_fisico, "on"))
        accion = _registrar_accion_sistema(db, toma, "encender", estado_anterior, "encendido", soc)
        acciones_salida.append(
            AccionControlEnergeticoOut(
                dispositivo_id=toma.id,
                nombre=toma.nombre,
                prioridad=prioridad_de(toma),
                accion_realizada="encender",
                umbral_aplicado=umbral if umbral is not None else soc,
                fecha_hora=accion.fecha_hora,
            )
        )

    # Alertas (R-15), en la misma transacción que las acciones.
    if decision.crear_alerta:
        _registrar_alerta_corte(db, domo, soc, decision.a_apagar, decision.umbral_alerta)
    elif decision.atender_alertas:
        _atender_alertas_corte(db, domo)

    domo_id_resultado = domo.id
    db.commit()

    # Recién ahora, con el estado ya persistido, se propaga la orden al hardware
    # real. Si el broker está caído, publicar_comando_toma loguea y devuelve False:
    # el resultado no cambia.
    for id_fisico, accion_mqtt in comandos_mqtt:
        publicar_comando_toma(domo_id=domo_id, accion=accion_mqtt, id_fisico=id_fisico, origen="r13")

    return EvaluarControlEnergeticoOut(
        domo_id=domo_id_resultado,
        estado_carga_soc=soc,
        umbral_soc_bajo=umbral_global_bajo,
        umbral_soc_recuperacion=umbral_global_recuperacion,
        acciones=acciones_salida,
        respetadas_por_primacia_humana=respetadas_salida,
    )
