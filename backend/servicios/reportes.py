"""
Reportes de un domo (GET /domos/{id}/reportes/resumen) y sus piezas, que también usa
tools/exportar_bi.py. Todo sale de consultas agregadas en la base: no hay límite de
lecturas ni se cargan en memoria.

Días y horas en hora de Bogotá. Colombia no cambia de hora, así que los límites del
rango se calculan con un desfase fijo de -5 h sin depender de tzdata en Windows. En
Postgres, la base convierte con 'America/Bogota'; en SQLite, que solo se usa en las
pruebas, con el mismo desfase.

Energía: se integra la potencia en el tiempo con trapecios entre lecturas seguidas.
Un hueco de más de 10 minutos no se integra. Cada tramo cuenta en el día de la lectura
con la que termina, así que un tramo que cruza la medianoche cae en el día nuevo.
Lo mismo aplica a las horas bajo la reserva: un tramo cuenta si la lectura con la que
empieza estaba bajo la reserva.

Modelo v2: las lecturas salen de la tabla `mediciones` (una fila por domo y bloque de
10 s; cada medida es una columna y NULL si no hubo dato) y las acciones de `acciones`,
cuyo domo sale por JOIN con `dispositivos`.

Cortes: la categoría es la actual de la toma, porque el historial no la guarda. El
tiempo apagada va desde el corte de R-13 hasta la siguiente acción sobre esa toma, o
hasta que se desactivó (`desactivado_en`), lo que pase primero; si no hay ninguna,
hasta ahora. Se recorta al rango consultado. Si una toma se reactiva, desactivado_en
se vacía y su corte vuelve a llegar hasta la siguiente acción: el periodo inactivo no
queda registrado en ninguna parte.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, Numeric, case, cast, extract, func, select
from sqlalchemy.orm import Session

from models import ACTOR_SISTEMA, ROL_API, Accion, Alerta, Dispositivo, Domo, Medicion
from servicios.categorias import CONFORT, IMPORTANTE, PRESCINDIBLE, RESERVA_SOC_POR_BATERIA, tipo_bateria_de

ZONA_BOGOTA = timezone(timedelta(hours=-5), "America/Bogota")
HUECO_MAXIMO_S = 600


def limites_utc(desde: date, hasta: date) -> tuple[datetime, datetime]:
    """Rango [inicio, fin) en UTC para unas fechas de Bogotá, ambas incluidas."""
    inicio = datetime.combine(desde, time.min, ZONA_BOGOTA)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, ZONA_BOGOTA)
    return inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc)


def _es_postgres(db) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _local(db, columna):
    return func.timezone("America/Bogota", columna) if _es_postgres(db) else func.datetime(columna, "-5 hours")


def dia_local(db, columna):
    local = _local(db, columna)
    return func.to_char(local, "YYYY-MM-DD") if _es_postgres(db) else func.strftime("%Y-%m-%d", local)


def hora_local(db, columna):
    local = _local(db, columna)
    return cast(extract("hour", local), Integer) if _es_postgres(db) else cast(func.strftime("%H", local), Integer)


def segundos_entre(db, fin, inicio):
    if _es_postgres(db):
        return extract("epoch", fin - inicio)
    # julianday pierde precisión: sin redondear, 10 minutos exactos darían 600,0000045 s.
    return func.round((func.julianday(fin, type_=Float) - func.julianday(inicio, type_=Float)) * 86400.0, 3, type_=Float)


def intervalos(db, modelo, campo: str, domo_id: int, inicio: Optional[datetime], fin: Optional[datetime]):
    """Subconsulta con cada lectura (t, v) y la anterior (t0, v0), por domo, sin las
    que no traen dato: una lectura vacía no parte un tramo por sí sola."""
    valor = getattr(modelo, campo)
    orden = (modelo.fecha_hora, modelo.id)
    consulta = select(
        modelo.fecha_hora.label("t"),
        valor.label("v"),
        func.lag(modelo.fecha_hora, type_=DateTime(timezone=True)).over(order_by=orden).label("t0"),
        func.lag(valor, type_=Float).over(order_by=orden).label("v0"),
    ).where(modelo.domo_id == domo_id, valor.is_not(None))
    if inicio is not None:
        consulta = consulta.where(modelo.fecha_hora >= inicio)
    if fin is not None:
        consulta = consulta.where(modelo.fecha_hora < fin)
    return consulta.subquery()


def wh_integrados(db, tramos):
    """Expresión: Wh de un tramo, o 0 si hay un hueco o es la primera lectura."""
    segundos = segundos_entre(db, tramos.c.t, tramos.c.t0)
    return case(
        (segundos <= HUECO_MAXIMO_S, (tramos.c.v + tramos.c.v0) / 2.0 * segundos / 3600.0),
        else_=0.0,
    )


def segundos_bajo_reserva(db, tramos, reserva: float):
    """Expresión: segundos de un tramo que empezó con el SoC bajo la reserva."""
    segundos = segundos_entre(db, tramos.c.t, tramos.c.t0)
    return case(((segundos <= HUECO_MAXIMO_S) & (tramos.c.v0 < reserva), segundos), else_=0.0)


def _kwh_por_dia(db, modelo, campo, domo_id, inicio, fin) -> dict[str, float]:
    tramos = intervalos(db, modelo, campo, domo_id, inicio, fin)
    dia = dia_local(db, tramos.c.t)
    filas = db.execute(select(dia.label("dia"), func.sum(wh_integrados(db, tramos)).label("wh")).group_by(dia)).all()
    return {f.dia: float(f.wh or 0) / 1000.0 for f in filas}


def _perfil_horario(db, modelo, campo, domo_id, inicio, fin) -> dict[int, float]:
    valor = getattr(modelo, campo)
    hora = hora_local(db, modelo.fecha_hora)
    filas = db.execute(
        select(hora.label("hora"), func.avg(valor).label("promedio"))
        .where(modelo.domo_id == domo_id, valor.is_not(None), modelo.fecha_hora >= inicio, modelo.fecha_hora < fin)
        .group_by(hora)
    ).all()
    return {int(f.hora): float(f.promedio) for f in filas}


def _soc_por_dia(db, domo_id, inicio, fin) -> dict[str, tuple[float, float, float]]:
    soc = Medicion.soc_pct
    dia = dia_local(db, Medicion.fecha_hora)
    filas = db.execute(
        select(dia.label("dia"), func.min(soc).label("minimo"), func.max(soc).label("maximo"), func.avg(soc).label("promedio"))
        .where(Medicion.domo_id == domo_id, soc.is_not(None), Medicion.fecha_hora >= inicio, Medicion.fecha_hora < fin)
        .group_by(dia)
    ).all()
    return {f.dia: (float(f.minimo), float(f.maximo), float(f.promedio)) for f in filas}


def _horas_bajo_reserva(db, domo_id, reserva, inicio, fin) -> float:
    tramos = intervalos(db, Medicion, "soc_pct", domo_id, inicio, fin)
    total = db.execute(select(func.sum(segundos_bajo_reserva(db, tramos, reserva)))).scalar()
    return float(total or 0) / 3600.0


def _a_utc(valor: datetime) -> datetime:
    # SQLite devuelve fechas sin zona; siempre están en UTC.
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor


def intervalos_de_corte(db, domo_id: int, inicio: Optional[datetime], fin: Optional[datetime], ahora: datetime) -> list[dict]:
    """Cada corte de R-13 que se cruza con [inicio, fin), recortado a ese rango. Sin
    límites, desde siempre hasta ahora."""
    inicio = inicio or datetime.min.replace(tzinfo=timezone.utc)
    fin = fin or ahora
    siguiente = (
        func.lead(Accion.fecha_hora, type_=DateTime(timezone=True))
        .over(partition_by=Accion.dispositivo_id, order_by=(Accion.fecha_hora, Accion.id))
        .label("siguiente")
    )
    # Sin límite inferior: un corte anterior al rango puede seguir vigente dentro de él.
    acciones = (
        select(
            Accion.dispositivo_id,
            Accion.actor_tipo,
            Accion.accion,
            Accion.fecha_hora,
            siguiente,
        )
        .join(Dispositivo, Dispositivo.id == Accion.dispositivo_id)
        .where(Dispositivo.domo_id == domo_id, Accion.fecha_hora < fin)
        .subquery()
    )
    filas = db.execute(
        select(
            acciones.c.fecha_hora,
            acciones.c.siguiente,
            Dispositivo.id,
            Dispositivo.nombre,
            Dispositivo.categoria,
            Dispositivo.desactivado_en,
            Dispositivo.activo,
        )
        .join(Dispositivo, Dispositivo.id == acciones.c.dispositivo_id)
        .where(acciones.c.actor_tipo == ACTOR_SISTEMA, acciones.c.accion == "apagar")
        .order_by(acciones.c.fecha_hora)
    ).all()

    limite = min(fin, ahora)
    resultado = []
    for fila in filas:
        comienzo = _a_utc(fila.fecha_hora)
        final = limite
        for corte in (fila.siguiente, fila.desactivado_en):
            if corte is not None:
                final = min(final, max(comienzo, _a_utc(corte)))
        desde_rango = max(comienzo, inicio)
        if final <= desde_rango and comienzo < inicio:
            continue
        resultado.append(
            {
                "dispositivo_id": fila.id,
                "nombre": fila.nombre,
                "categoria": fila.categoria,
                "activo": fila.activo,
                "inicio": desde_rango,
                "fin": max(final, desde_rango),
                "empieza_en_el_rango": comienzo >= inicio,
                "horas": max(0.0, (final - desde_rango).total_seconds()) / 3600.0,
            }
        )
    return resultado


def _cortes(db, domo_id, inicio, fin, ahora) -> tuple[list[dict], list[dict]]:
    por_categoria = {PRESCINDIBLE: 0, CONFORT: 0, IMPORTANTE: 0}
    por_toma: dict[int, dict] = {}
    for corte in intervalos_de_corte(db, domo_id, inicio, fin, ahora):
        toma = por_toma.setdefault(
            corte["dispositivo_id"],
            {
                "dispositivo_id": corte["dispositivo_id"],
                "nombre": corte["nombre"],
                "categoria": corte["categoria"],
                "activo": corte["activo"],
                "cortes": 0,
                "horas_apagada": 0.0,
            },
        )
        if corte["empieza_en_el_rango"]:
            toma["cortes"] += 1
            por_categoria[corte["categoria"]] = por_categoria.get(corte["categoria"], 0) + 1
        toma["horas_apagada"] += corte["horas"]

    lista_categorias = [{"categoria": categoria, "cortes": total} for categoria, total in por_categoria.items()]
    lista_tomas = sorted(por_toma.values(), key=lambda t: (-t["horas_apagada"], t["dispositivo_id"]))
    return lista_categorias, lista_tomas


def _acciones(db, domo_id, inicio, fin) -> dict:
    filas = db.execute(
        select(Accion.actor_tipo, func.count().label("total"))
        .join(Dispositivo, Dispositivo.id == Accion.dispositivo_id)
        .where(Dispositivo.domo_id == domo_id, Accion.fecha_hora >= inicio, Accion.fecha_hora < fin)
        .group_by(Accion.actor_tipo)
    ).all()
    automaticas = sum(f.total for f in filas if f.actor_tipo == ACTOR_SISTEMA)
    manuales = sum(f.total for f in filas if f.actor_tipo != ACTOR_SISTEMA)
    # El rol con el nombre de v1 ('dueño', 'administrador', 'sistema'), como lo muestra el frontend.
    por_rol = sorted(
        ({"rol": ROL_API.get(f.actor_tipo, f.actor_tipo), "total": f.total} for f in filas),
        key=lambda r: (-r["total"], r["rol"]),
    )
    return {"automaticas": automaticas, "manuales": manuales, "por_rol": por_rol}


def _alertas(db, domo_id, inicio, fin) -> dict:
    en_rango = (Alerta.domo_id == domo_id, Alerta.fecha_hora >= inicio, Alerta.fecha_hora < fin)
    filas = db.execute(
        select(Alerta.tipo_alerta, Alerta.nivel_prioridad, func.count().label("total"))
        .where(*en_rango)
        .group_by(Alerta.tipo_alerta, Alerta.nivel_prioridad)
    ).all()
    segundos = segundos_entre(db, Alerta.fecha_atendida, Alerta.fecha_hora)
    fila = db.execute(
        select(func.avg(cast(segundos, Numeric)).label("promedio"), func.count().label("total"))
        .where(*en_rango, Alerta.fecha_atendida.is_not(None))
    ).one()
    por_tipo = sorted(
        ({"tipo_alerta": f.tipo_alerta, "nivel_prioridad": f.nivel_prioridad, "total": f.total} for f in filas),
        key=lambda a: (-a["total"], a["tipo_alerta"] or "", a["nivel_prioridad"] or ""),
    )
    return {
        "total": sum(f.total for f in filas),
        "por_tipo": por_tipo,
        "minutos_medios_hasta_atendida": float(fila.promedio) / 60.0 if fila.promedio is not None else None,
        "alertas_con_tiempo": fila.total,
    }


def resumen_del_domo(db: Session, domo_id: int, desde: date, hasta: date, ahora: Optional[datetime] = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    inicio, fin = limites_utc(desde, hasta)
    domo = db.get(Domo, domo_id)
    tipo_bateria = tipo_bateria_de(domo)
    reserva = RESERVA_SOC_POR_BATERIA[tipo_bateria]

    generada = _kwh_por_dia(db, Medicion, "potencia_fv_w", domo_id, inicio, fin)
    consumida = _kwh_por_dia(db, Medicion, "potencia_consumo_w", domo_id, inicio, fin)
    soc = _soc_por_dia(db, domo_id, inicio, fin)
    perfil_generacion = _perfil_horario(db, Medicion, "potencia_fv_w", domo_id, inicio, fin)
    perfil_consumo = _perfil_horario(db, Medicion, "potencia_consumo_w", domo_id, inicio, fin)
    cortes_por_categoria, cortes_por_toma = _cortes(db, domo_id, inicio, fin, ahora)

    dias = []
    dia = desde
    while dia <= hasta:
        clave = dia.isoformat()
        minimo, maximo, promedio = soc.get(clave, (None, None, None))
        dias.append(
            {
                "fecha": clave,
                "kwh_generados": generada.get(clave),
                "kwh_consumidos": consumida.get(clave),
                "soc_minimo": minimo,
                "soc_maximo": maximo,
                "soc_promedio": promedio,
            }
        )
        dia += timedelta(days=1)

    total_generado = sum(generada.values())
    total_consumido = sum(consumida.values())
    return {
        "domo_id": domo_id,
        "desde": desde,
        "hasta": hasta,
        "zona_horaria": "America/Bogota",
        "kwh_generados": total_generado,
        "kwh_consumidos": total_consumido,
        "balance_kwh": total_generado - total_consumido,
        "es_demostracion": bool(getattr(domo, "es_demostracion", False)),
        "tipo_bateria": tipo_bateria,
        "reserva_soc": reserva,
        "horas_bajo_reserva": _horas_bajo_reserva(db, domo_id, reserva, inicio, fin),
        "dias": dias,
        "perfil_horario": [
            {"hora": h, "generacion_w": perfil_generacion.get(h), "consumo_w": perfil_consumo.get(h)} for h in range(24)
        ],
        "cortes_por_categoria": cortes_por_categoria,
        "cortes_por_toma": cortes_por_toma,
        "acciones": _acciones(db, domo_id, inicio, fin),
        "alertas": _alertas(db, domo_id, inicio, fin),
    }
