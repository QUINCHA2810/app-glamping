"""
Genera un domo de DEMOSTRACIÓN con datos sintéticos, para ver Reportes y Power BI con
un mes de datos sin hardware. No es un domo real, y la app lo marca siempre como
"Demostración · datos sintéticos" (domos.es_demostracion).

Modelo v2 (docs/modelo_v2/MAPEO.md): escribe en `domos` (con su asignación 'dueno'),
`dispositivos`, `mediciones` (una fila cada 5 minutos con batería, generación y
consumo), `acciones` y `alertas`. Los umbrales de R-13 se calculan con la categoría y
el perfil, igual que en el backend.

Desde backend/, con el entorno virtual activo y alembic_v2 en head:

    python tools/generar_datos_demo.py --dueno-correo dueno@ejemplo.co --solo-calcular
    python tools/generar_datos_demo.py --dueno-correo dueno@ejemplo.co
    python tools/generar_datos_demo.py --dueno-correo dueno@ejemplo.co --reiniciar

Qué hace:
- Busca, entre los domos de los que es dueño el indicado, el "Domo demostración". Si
  no existe lo crea (con la asignación 'dueno'), con
  es_demostracion = true. Si hay un domo con ese nombre que no es de demostración, si
  --domo-id no apunta a un domo de demostración de ese dueño con ese nombre, o si el
  domo ya tiene datos y no se pidió --reiniciar, se detiene sin escribir nada.
- Configura en el domo la batería (AGM, 1200 Wh) y el perfil (equilibrado), y crea cuatro
  dispositivos SIN id_fisico, así que nada se publica nunca a una toma real.
- Calcula --dias días, cada 5 minutos y terminando en la hora actual: generación
  (curva solar de Bogotá con un factor de nubes por día), consumo por dispositivo y
  SoC integrado desde el balance. Con la misma --semilla, los mismos días dan los mismos
  datos.
- Aplica R-13 en cada lectura con un reloj simulado, con las mismas reglas que el
  backend (servicios/control_energetico.decidir_r13 y los presets de
  servicios/categorias.py): una toma cortada deja de consumir hasta que se reactiva, y
  se registran las acciones y las alertas que el sistema habría registrado. Agrega
  algunas acciones manuales del dueño.
- Muestra un resumen y pide confirmación con "s". --solo-calcular muestra el resumen
  sin escribir: en Postgres abre la transacción como de solo lectura.
- Escribe todo en lotes dentro de UNA transacción: si algo falla, no queda nada.
  --reiniciar borra antes SOLO los datos del domo de demostración.

Nunca imprime la DATABASE_URL: ante un error muestra solo su tipo.
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

NOMBRE_DEMO = "Domo demostración"
UBICACION_DEMO = "Datos sintéticos, no es un domo real"

PASO = timedelta(minutes=5)
HORAS_PASO = PASO.total_seconds() / 3600.0
# Bogotá no tiene horario de verano: un desfase fijo basta y no depende de tzdata.
ZONA_BOGOTA = timezone(timedelta(hours=-5))
AMANECER_H = 5.75  # 5:45
ATARDECER_H = 17 + 55 / 60  # 17:55
SOC_INICIAL = 80.0
EFICIENCIA_CARGA = 0.9
# ESP32, sensores y relés: siempre encendidos, y no son un dispositivo de la app.
CONSUMO_MONITOREO_W = 4.0
LOTE = 1000

# (clave, nombre, tipo, categoría, ubicación)
DISPOSITIVOS = (
    ("bombillo", "Bombillo", "bombillo", "esencial", "Sala"),
    ("nevera", "Nevera", "toma", "importante", "Cocina"),
    ("cargadores", "Cargadores", "toma", "confort", "Mesa de noche"),
    ("luces", "Luces decorativas", "toma", "prescindible", "Terraza"),
)

# Días, según su factor de nubes: el que cuenta como "nublado" en el resumen.
DESPEJADO, PARCIAL, NUBLADO = "despejado", "parcialmente nublado", "nublado"


class Detenido(Exception):
    """Algo no cuadra: no se escribe nada. El mensaje es para el usuario."""


@dataclass
class Parametros:
    dias: int = 30
    wp: float = 500.0
    semilla: int = 2026
    tipo_bateria: str = "agm"
    capacidad_wh: float = 1200.0
    perfil: str = "equilibrado"
    acciones_manuales: bool = True


@dataclass
class TomaSim:
    """Lo que decidir_r13 necesita de un dispositivo (id y categoría). `id` es
    provisional y sigue el orden de creación, como los id reales: el desempate de R-13
    por id es el mismo."""

    id: int
    clave: str
    nombre: str
    tipo: str
    categoria: str
    ubicacion: str
    estado: str = "encendido"
    activo: bool = True
    ultima: Optional[tuple[str, str]] = None  # (actor_tipo, acción) de su última acción
    # Primacía humana vigente (DB-4): la encendió el dueño y desde entonces el SoC no ha
    # llegado a su umbral de reactivación. Ver servicios/control_energetico.primacias_humanas.
    primacia: bool = False


@dataclass
class Dia:
    fecha: date
    tipo: str
    nubes: float
    rng: random.Random
    bombillo: tuple[float, float]
    luces: tuple[float, float]
    cargadores_en_la_noche: bool
    fase_nevera: int


@dataclass
class Simulacion:
    parametros: Parametros
    inicio: datetime
    fin: datetime
    tomas: list[TomaSim]
    lecturas_bateria: list[dict] = field(default_factory=list)
    lecturas_fotovoltaicas: list[dict] = field(default_factory=list)
    lecturas_consumo: list[dict] = field(default_factory=list)
    acciones: list[dict] = field(default_factory=list)  # columnas de `acciones`, con "clave" en lugar de dispositivo_id
    alertas: list[dict] = field(default_factory=list)
    dias: list[Dia] = field(default_factory=list)
    traza: list[dict] = field(default_factory=list)


def fin_por_defecto(ahora: Optional[datetime] = None) -> datetime:
    """La hora actual, redondeada hacia abajo a 5 minutos."""
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ahora.replace(second=0, microsecond=0) - timedelta(minutes=ahora.minute % 5)


def tomas_iniciales(parametros: Parametros) -> list[TomaSim]:
    return [
        TomaSim(
            id=indice,
            clave=clave,
            nombre=nombre,
            tipo=tipo,
            categoria=categoria,
            ubicacion=ubicacion,
            estado="apagado" if tipo == "bombillo" else "encendido",
        )
        for indice, (clave, nombre, tipo, categoria, ubicacion) in enumerate(DISPOSITIVOS, start=1)
    ]


def _dia(fecha: date, semilla: int) -> Dia:
    """Todo lo aleatorio de un día sale de la semilla y la fecha: el mismo día da lo
    mismo aunque la ventana empiece en otro momento."""
    rng = random.Random(f"vatia-demo:{semilla}:{fecha.isoformat()}")
    sorteo = rng.random()
    if sorteo < 0.5:
        tipo, nubes = DESPEJADO, rng.uniform(0.85, 1.0)
    elif sorteo < 0.8:
        tipo, nubes = PARCIAL, rng.uniform(0.55, 0.85)
    else:
        tipo, nubes = NUBLADO, rng.uniform(0.2, 0.5)
    return Dia(
        fecha=fecha,
        tipo=tipo,
        nubes=nubes,
        rng=rng,
        bombillo=(18.0 + rng.uniform(0, 0.75), 22.5 + rng.uniform(0, 1.4)),
        luces=(18.25 + rng.uniform(0, 0.5), 23.0 + rng.uniform(0, 0.9)),
        cargadores_en_la_noche=rng.random() > 0.15,
        fase_nevera=rng.randrange(45),
    )


def generacion_w(hora: float, dia: Dia, wp: float, eficiencia: float) -> float:
    if not AMANECER_H <= hora < ATARDECER_H:
        return 0.0
    sol = math.sin(math.pi * (hora - AMANECER_H) / (ATARDECER_H - AMANECER_H))
    variacion = 0.97 + 0.03 * dia.rng.random() if dia.tipo == DESPEJADO else 0.75 + 0.25 * dia.rng.random()
    return wp * eficiencia * sol * dia.nubes * variacion


def consumo_w(toma: TomaSim, hora: float, minuto_del_dia: int, dia: Dia) -> float:
    """Lo que consumiría si está encendida. Perfiles por hora: nevera cíclica todo el
    día, cargadores en la tarde y la noche, luces y bombillo de noche."""
    if toma.clave == "bombillo":
        desde, hasta = dia.bombillo
        return 9.0 if desde <= hora < hasta or 5.0 <= hora < 6.0 else 0.0
    if toma.clave == "nevera":
        # Trabaja más en las horas de calor.
        ciclo = 0.30 + (0.08 * math.sin(math.pi * (hora - 6) / 12) if 6 <= hora < 18 else -0.02)
        return 65.0 if (minuto_del_dia + dia.fase_nevera) % 45 < ciclo * 45 else 1.5
    if toma.clave == "cargadores":
        if dia.cargadores_en_la_noche and 18.5 <= hora < 23.0:
            return 18.0 + 14.0 * dia.rng.random()
        if 6.5 <= hora < 8.0:
            return 10.0
        return 1.0
    if toma.clave == "luces":
        desde, hasta = dia.luces
        return 14.0 if desde <= hora < hasta else 0.0
    return 0.0


def _eventos_manuales(dia: Dia, tomas: dict[str, TomaSim], hasta_manana: list) -> list[tuple[datetime, str, str]]:
    """Pocas acciones del dueño: a veces apaga las luces decorativas en la noche y las
    vuelve a encender al día siguiente; a veces vuelve a encender los cargadores que
    cortó el sistema."""
    base = datetime(dia.fecha.year, dia.fecha.month, dia.fecha.day, tzinfo=ZONA_BOGOTA)
    eventos = [(base + timedelta(hours=h), clave, accion) for h, clave, accion in hasta_manana]
    hasta_manana.clear()
    if dia.rng.random() < 0.3:
        eventos.append((base + timedelta(hours=21.5 + dia.rng.uniform(0, 1.5)), "luces", "apagar"))
        hasta_manana.append((18.0 + dia.rng.uniform(0, 0.5), "luces", "encender"))
    if dia.rng.random() < 0.12:
        eventos.append((base + timedelta(hours=19.0 + dia.rng.uniform(0, 1.0)), "cargadores", "encender"))
    return sorted(eventos)


def simular(parametros: Parametros, fin: datetime, con_traza: bool = False) -> Simulacion:
    from simulador_telemetria import EFICIENCIA_SISTEMA, VOLTAJE_AC_CARGAS, VOLTAJE_PANEL_MPP, _voltaje_bateria, armar_lecturas
    from servicios.categorias import RESERVA_SOC_POR_BATERIA, umbrales_de
    from servicios.control_energetico import (
        condicion_control_sistema,
        decidir_r13,
        descripcion_alerta_corte,
        nivel_alerta_corte,
    )

    validar(parametros)
    fin = fin.astimezone(timezone.utc)
    inicio = fin - timedelta(days=parametros.dias)
    tomas = tomas_iniciales(parametros)
    por_clave = {t.clave: t for t in tomas}
    sim = Simulacion(parametros=parametros, inicio=inicio, fin=fin, tomas=tomas)
    reserva = RESERVA_SOC_POR_BATERIA[parametros.tipo_bateria]
    capacidad = parametros.capacidad_wh

    soc = SOC_INICIAL
    alerta_activa: Optional[dict] = None
    dia: Optional[Dia] = None
    pendientes: list[tuple[datetime, str, str]] = []
    para_manana: list = []
    pasos = parametros.dias * 288

    for k in range(1, pasos + 1):
        t = inicio + k * PASO
        medio = (t - PASO / 2).astimezone(ZONA_BOGOTA)
        if dia is None or medio.date() != dia.fecha:
            dia = _dia(medio.date(), parametros.semilla)
            sim.dias.append(dia)
            if parametros.acciones_manuales:
                pendientes.extend(_eventos_manuales(dia, por_clave, para_manana))

        # Acciones del dueño dentro de este intervalo, antes de la lectura que lo cierra.
        while pendientes and pendientes[0][0] <= t:
            cuando, clave, accion = pendientes.pop(0)
            if cuando <= t - PASO:
                continue
            toma = por_clave[clave]
            nuevo = "encendido" if accion == "encender" else "apagado"
            if toma.estado == nuevo:
                continue
            sim.acciones.append(_accion(clave, "dueno", accion, toma.estado, nuevo, "control manual", cuando.replace(microsecond=0)))
            toma.estado, toma.ultima = nuevo, ("dueno", accion)
            toma.primacia = accion == "encender" and toma.categoria != "esencial"

        # Energía del intervalo.
        hora = medio.hour + medio.minute / 60 + medio.second / 3600
        minuto = medio.hour * 60 + medio.minute
        # Una toma cortada o apagada no consume. El bombillo lo usan los huéspedes, por horario.
        por_toma = {toma.clave: consumo_w(toma, hora, minuto, dia) if toma.estado == "encendido" else 0.0 for toma in tomas if toma.tipo == "toma"}
        por_toma["bombillo"] = consumo_w(por_clave["bombillo"], hora, minuto, dia)
        por_clave["bombillo"].estado = "encendido" if por_toma["bombillo"] > 0 else "apagado"
        consumida = sum(por_toma.values()) + CONSUMO_MONITOREO_W
        posible = generacion_w(hora, dia, parametros.wp, EFICIENCIA_SISTEMA)
        generada = posible
        if posible > consumida:
            carga_wh = (posible - consumida) * HORAS_PASO * EFICIENCIA_CARGA
            espacio_wh = (100.0 - soc) / 100.0 * capacidad
            if carga_wh > espacio_wh:
                # Batería llena: el controlador reduce lo que toma de los paneles.
                carga_wh = espacio_wh
                generada = consumida + espacio_wh / (EFICIENCIA_CARGA * HORAS_PASO)
            soc = min(100.0, soc + carga_wh / capacidad * 100.0)
        else:
            soc = max(0.0, soc + (posible - consumida) * HORAS_PASO / capacidad * 100.0)
        balance = generada - consumida
        temperatura = 23.0 + 3.0 * math.sin(math.pi * (hora - 9) / 12) + abs(balance) / 100.0
        lecturas = armar_lecturas(
            soc,
            generada,
            consumida,
            balance,
            HORAS_PASO,
            _voltaje_bateria(soc, balance),
            VOLTAJE_PANEL_MPP if generada > 0 else 0.0,
            VOLTAJE_AC_CARGAS,
            temperatura,
        )
        sim.lecturas_bateria.append({**lecturas["bateria"], "fecha_hora": t})
        sim.lecturas_fotovoltaicas.append({**lecturas["fotovoltaica"], "fecha_hora": t})
        sim.lecturas_consumo.append({**lecturas["consumo"], "fecha_hora": t})

        # R-13 sobre la lectura recién guardada, como el listener: con el SoC redondeado.
        soc_guardado = lecturas["bateria"]["estado_carga_soc"]
        cuando = t + timedelta(seconds=1)
        encendidas = [x for x in tomas if x.tipo == "toma" and x.activo and x.estado == "encendido" and x.categoria != "esencial"]
        cortadas = [x for x in tomas if x.tipo == "toma" and x.activo and x.ultima == ("sistema", "apagar")]
        # Primacía humana: la lectura recién guardada es posterior a las acciones del dueño
        # de este intervalo; si llega a la reactivación de la toma, la primacía se acaba.
        for toma in encendidas:
            if toma.primacia and soc_guardado >= umbrales_de(toma.categoria, parametros.tipo_bateria, parametros.perfil)[1]:
                toma.primacia = False
        con_primacia = {x.id for x in encendidas if x.primacia}
        decision = decidir_r13(
            soc_guardado, encendidas, cortadas, parametros.tipo_bateria, parametros.perfil, con_primacia
        )
        condicion = condicion_control_sistema(soc_guardado)
        for toma in decision.a_apagar:
            motivo = "reserva" if toma.id in decision.cortadas_por_reserva else None
            sim.acciones.append(
                _accion(toma.clave, "sistema", "apagar", toma.estado, "apagado", condicion_control_sistema(soc_guardado, motivo), cuando)
            )
            toma.estado, toma.ultima, toma.primacia = "apagado", ("sistema", "apagar"), False
        for toma in decision.a_reactivar:
            sim.acciones.append(_accion(toma.clave, "sistema", "encender", toma.estado, "encendido", condicion, cuando))
            toma.estado, toma.ultima, toma.primacia = "encendido", ("sistema", "encender"), False
        if decision.crear_alerta and alerta_activa is None:
            alerta_activa = {
                "tipo": "bateria_baja",
                "mensaje": descripcion_alerta_corte(decision.a_apagar),
                "nivel": nivel_alerta_corte(soc_guardado, reserva),
                "variable": "estado_carga_soc",
                "valor_detectado": soc_guardado,
                "limite": decision.umbral_alerta,
                "estado": "activa",
                "creada_por_r13": True,
                "fecha_hora": cuando,
                "fecha_atendida": None,
            }
            sim.alertas.append(alerta_activa)
        elif decision.atender_alertas and alerta_activa is not None:
            alerta_activa["estado"] = "atendida"
            alerta_activa["fecha_atendida"] = cuando
            alerta_activa = None

        if con_traza:
            sim.traza.append(
                {
                    "t": t,
                    "soc": soc_guardado,
                    "consumo_por_toma": por_toma,
                    "encendidas": {x.clave for x in tomas if x.tipo == "toma" and x.estado == "encendido"},
                }
            )
    return sim


def _accion(clave, actor_tipo, accion, anterior, nuevo, condicion, cuando) -> dict:
    """Una fila de `acciones` (sin actor_ref ni dispositivo_id: los pone escribir)."""
    return {
        "clave": clave,
        "actor_tipo": actor_tipo,
        "accion": accion,
        "estado_anterior": anterior,
        "estado_nuevo": nuevo,
        "condicion": condicion,
        "resultado": "ok",
        "fecha_hora": cuando,
    }


def validar(parametros: Parametros) -> None:
    from servicios.categorias import PERFILES, RESERVA_SOC_POR_BATERIA

    if not 1 <= parametros.dias <= 366:
        raise Detenido("--dias debe estar entre 1 y 366.")
    if not 0 < parametros.wp <= 5000:
        raise Detenido("--wp debe ser mayor que 0 y como mucho 5000.")
    if not 0 < parametros.capacidad_wh <= 100000:
        raise Detenido("--capacidad-wh debe ser mayor que 0 y como mucho 100000.")
    if parametros.tipo_bateria not in RESERVA_SOC_POR_BATERIA:
        raise Detenido(f"--tipo-bateria debe ser uno de: {', '.join(RESERVA_SOC_POR_BATERIA)}.")
    if parametros.perfil not in PERFILES:
        raise Detenido(f"--perfil debe ser uno de: {', '.join(PERFILES)}.")


# ---------------------------------------------------------------- base de datos


def buscar_dueno(db, correo: str):
    from models import Usuario

    dueno = db.query(Usuario).filter(Usuario.correo == correo.strip()).first()
    if dueno is None or dueno.rol != "dueño":
        raise Detenido("No hay ninguna cuenta de dueño con ese correo.")
    return dueno


def buscar_domo_demo(db, dueno, domo_id: Optional[int]):
    """El domo de demostración del dueño, o None si hay que crearlo. Se detiene ante
    cualquier cosa que no cuadre: así nunca se escribe en un domo real."""
    from models import ROL_DUENO, Asignacion, Domo

    if domo_id is not None:
        domo = db.get(Domo, domo_id)
        if domo is None or domo.propietario_id != dueno.id:
            raise Detenido(f"El domo {domo_id} no existe o no es de ese dueño.")
        if not domo.es_demostracion or domo.nombre != NOMBRE_DEMO:
            raise Detenido(f"El domo {domo_id} no es el domo de demostración (\"{NOMBRE_DEMO}\" con es_demostracion = true). No se escribe nada.")
        return domo
    candidatos = (
        db.query(Domo)
        .join(Asignacion, Asignacion.domo_id == Domo.id)
        .filter(Asignacion.usuario_id == dueno.id, Asignacion.rol == ROL_DUENO, Domo.nombre == NOMBRE_DEMO)
        .order_by(Domo.id)
        .all()
    )
    if any(not d.es_demostracion for d in candidatos):
        raise Detenido(f"El dueño tiene un domo llamado \"{NOMBRE_DEMO}\" que no es de demostración. No se escribe nada.")
    if len(candidatos) > 1:
        raise Detenido(f"Hay {len(candidatos)} domos de demostración con ese nombre: elige uno con --domo-id.")
    return candidatos[0] if candidatos else None


def filas_del_domo(db, domo_id: int) -> dict[str, int]:
    from sqlalchemy import func, select

    from models import Accion, Alerta, Dispositivo, Medicion

    def contar(consulta):
        return db.execute(consulta).scalar_one()

    dispositivos = select(Dispositivo.id).where(Dispositivo.domo_id == domo_id)
    return {
        "mediciones": contar(select(func.count()).select_from(Medicion).where(Medicion.domo_id == domo_id)),
        "acciones": contar(select(func.count()).select_from(Accion).where(Accion.dispositivo_id.in_(dispositivos))),
        "alertas": contar(select(func.count()).select_from(Alerta).where(Alerta.domo_id == domo_id)),
        "dispositivos": contar(select(func.count()).select_from(Dispositivo).where(Dispositivo.domo_id == domo_id)),
    }


def borrar_datos_del_domo(db, domo) -> None:
    """Solo del domo de demostración: lo comprueba otra vez antes de borrar. Las
    acciones primero: la base no deja borrar un dispositivo que tiene acciones."""
    from sqlalchemy import delete, select

    from models import Accion, Alerta, Dispositivo, Medicion

    if not domo.es_demostracion or domo.nombre != NOMBRE_DEMO:
        raise Detenido("Ese domo no es de demostración: no se borra nada.")
    dispositivos = select(Dispositivo.id).where(Dispositivo.domo_id == domo.id)
    db.execute(delete(Accion).where(Accion.dispositivo_id.in_(dispositivos)))
    for modelo in (Alerta, Medicion):
        db.execute(delete(modelo).where(modelo.domo_id == domo.id))
    db.execute(delete(Dispositivo).where(Dispositivo.domo_id == domo.id))


def mediciones_de(sim: Simulacion, domo_id: int) -> list[dict]:
    """Una fila de `mediciones` por paso: las tres lecturas de un paso comparten la
    hora, múltiplo de 5 minutos, así que caen en el mismo bloque de 10 s."""
    from servicios.mediciones import columnas_de

    filas = []
    for bateria, fv, consumo in zip(sim.lecturas_bateria, sim.lecturas_fotovoltaicas, sim.lecturas_consumo):
        filas.append(
            {
                "domo_id": domo_id,
                "fecha_hora": bateria["fecha_hora"],
                **columnas_de("bateria", bateria),
                **columnas_de("fotovoltaica", fv),
                **columnas_de("consumo", consumo),
            }
        )
    return filas


def escribir(db, dueno, domo, sim: Simulacion, reiniciar: bool):
    """Todo en la sesión, sin commit: lo hace quien llama, una sola vez."""
    from sqlalchemy import insert

    from models import ACTOR_REF_R13, ROL_DUENO, Accion, Alerta, Asignacion, Dispositivo, Domo, Medicion

    if domo is None:
        domo = Domo(nombre=NOMBRE_DEMO, ubicacion=UBICACION_DEMO, es_demostracion=True)
        db.add(domo)
        db.flush()
        db.add(Asignacion(usuario_id=dueno.id, domo_id=domo.id, rol=ROL_DUENO))
    elif reiniciar:
        borrar_datos_del_domo(db, domo)
    if not domo.es_demostracion:
        raise Detenido("Ese domo no es de demostración: no se escribe nada.")

    domo.bateria_tipo = sim.parametros.tipo_bateria
    domo.bateria_capacidad_wh = sim.parametros.capacidad_wh
    domo.perfil = sim.parametros.perfil

    ids = {}
    for toma in sim.tomas:
        dispositivo = Dispositivo(
            domo_id=domo.id,
            tipo=toma.tipo,
            nombre=toma.nombre,
            ubicacion=toma.ubicacion,
            categoria=toma.categoria,
            estado=toma.estado,
            id_fisico=None,
            activo=True,
        )
        db.add(dispositivo)
        db.flush()
        ids[toma.clave] = dispositivo.id

    def en_lotes(modelo, filas):
        for i in range(0, len(filas), LOTE):
            db.execute(insert(modelo), filas[i : i + LOTE])

    en_lotes(Medicion, mediciones_de(sim, domo.id))
    acciones = []
    for a in sim.acciones:
        fila = {k: v for k, v in a.items() if k != "clave"}
        fila.update(
            dispositivo_id=ids[a["clave"]],
            actor_ref=ACTOR_REF_R13 if a["actor_tipo"] == "sistema" else dueno.correo,
        )
        acciones.append(fila)
    en_lotes(Accion, acciones)
    en_lotes(Alerta, [{**a, "domo_id": domo.id} for a in sim.alertas])
    return domo


def resumen(sim: Simulacion, domo, existentes: dict[str, int], reiniciar: bool) -> str:
    def local(t):
        return t.astimezone(ZONA_BOGOTA).strftime("%Y-%m-%d %H:%M")

    socs = [f["estado_carga_soc"] for f in sim.lecturas_bateria]
    kwh_gen = sum(f["energia_generada"] for f in sim.lecturas_fotovoltaicas) / 1000
    kwh_con = sum(f["energia_consumida"] for f in sim.lecturas_consumo) / 1000
    sistema = [a for a in sim.acciones if a["actor_tipo"] == "sistema"]
    cortes = {}
    categoria = {t.clave: t.categoria for t in sim.tomas}
    for a in sistema:
        if a["accion"] == "apagar":
            cortes[categoria[a["clave"]]] = cortes.get(categoria[a["clave"]], 0) + 1
    nublados = [d for d in sim.dias if d.tipo == NUBLADO]
    criticas = sum(1 for a in sim.alertas if a["nivel"] == "critico")
    p = sim.parametros
    destino = f'"{NOMBRE_DEMO}" (se crea)' if domo is None else f'"{domo.nombre}" (id {domo.id})'
    lineas = [
        f"Domo: {destino}",
        f"Batería {p.tipo_bateria.upper()} de {p.capacidad_wh:g} Wh, perfil {p.perfil}, {p.wp:g} Wp, semilla {p.semilla}",
        f"Rango (hora de Bogotá): {local(sim.inicio + PASO)} a {local(sim.fin)} — {p.dias} días",
        f"Días: {len(sim.dias)} con datos; nublados: {len(nublados)}"
        + (f" ({', '.join(d.fecha.isoformat() for d in nublados)})" if nublados else ""),
        f"SoC: mínimo {min(socs):.1f} %, máximo {max(socs):.1f} %. Generado {kwh_gen:.2f} kWh, consumido {kwh_con:.2f} kWh",
        "Filas a escribir:",
        f"  mediciones              {len(sim.lecturas_bateria)} (batería, generación y consumo cada 5 minutos)",
        f"  acciones                {len(sim.acciones)} ({len(sistema)} del sistema, {len(sim.acciones) - len(sistema)} manuales del dueño)",
        f"  alertas                 {len(sim.alertas)} ({criticas} críticas, {len(sim.alertas) - criticas} advertencias)",
        f"  dispositivos            {len(sim.tomas)} (sin id_fisico)",
        f"  domos                   {1 if domo is None else 0} (batería y perfil en el propio domo)",
        "Cortes de R-13 por categoría: " + (", ".join(f"{c} {n}" for c, n in sorted(cortes.items())) or "ninguno"),
    ]
    if reiniciar and any(existentes.values()):
        lineas.append("Se borran antes, solo de este domo: " + ", ".join(f"{k} {v}" for k, v in existentes.items() if v))
    return "\n".join(lineas)


def ejecutar(
    db,
    dueno_correo: str,
    parametros: Parametros,
    domo_id: Optional[int] = None,
    solo_calcular: bool = False,
    reiniciar: bool = False,
    preguntar: Callable[[str], str] = input,
    decir: Callable[[str], None] = print,
    fin: Optional[datetime] = None,
) -> bool:
    """True si escribió. Ante cualquier error deshace la transacción: no queda nada."""
    try:
        return _ejecutar(db, dueno_correo, parametros, domo_id, solo_calcular, reiniciar, preguntar, decir, fin)
    except BaseException:
        db.rollback()
        raise


def _ejecutar(db, dueno_correo, parametros, domo_id, solo_calcular, reiniciar, preguntar, decir, fin) -> bool:
    validar(parametros)
    dueno = buscar_dueno(db, dueno_correo)
    domo = buscar_domo_demo(db, dueno, domo_id)
    existentes = filas_del_domo(db, domo.id) if domo is not None else {}
    if any(existentes.values()) and not reiniciar:
        raise Detenido("El domo de demostración ya tiene datos. Usa --reiniciar para borrarlos y generarlos de nuevo.")

    sim = simular(parametros, fin or fin_por_defecto())
    decir(resumen(sim, domo, existentes, reiniciar))
    if solo_calcular:
        decir("Solo cálculo: no se escribió nada.")
        return False
    if preguntar("¿Escribir estos datos en el domo de demostración? Escribe s para continuar: ").strip().lower() not in ("s", "si", "sí"):
        decir("Cancelado. No se escribió nada.")
        return False
    domo = escribir(db, dueno, domo, sim, reiniciar)
    db.commit()
    decir(f"Listo: datos escritos en \"{NOMBRE_DEMO}\" (id {domo.id}).")
    return True


def main() -> None:
    from servicios.categorias import PERFILES, RESERVA_SOC_POR_BATERIA

    parser = argparse.ArgumentParser(description="Domo de demostración con datos sintéticos (ver el docstring).")
    parser.add_argument("--dueno-correo", required=True, help="correo del dueño al que pertenece el domo de demostración")
    parser.add_argument("--domo-id", type=int, help="id del domo de demostración, si hay más de uno")
    parser.add_argument("--dias", type=int, default=30, help="días hacia atrás desde ahora (30)")
    parser.add_argument("--wp", type=float, default=500.0, help="potencia pico de los paneles, en Wp (500, como el simulador)")
    parser.add_argument("--semilla", type=int, default=2026, help="semilla del clima y del uso: la misma da los mismos datos (2026)")
    parser.add_argument("--tipo-bateria", default="agm", choices=sorted(RESERVA_SOC_POR_BATERIA), help="agm (por defecto) o lifepo4")
    parser.add_argument("--capacidad-wh", type=float, default=1200.0, help="capacidad de la batería en Wh (1200)")
    parser.add_argument("--perfil", default="equilibrado", choices=PERFILES, help="perfil de control (equilibrado)")
    parser.add_argument("--sin-acciones-manuales", action="store_true", help="sin acciones manuales del dueño")
    parser.add_argument("--solo-calcular", action="store_true", help="muestra el resumen sin escribir nada")
    parser.add_argument("--reiniciar", action="store_true", help="borra los datos del domo de demostración y los genera de nuevo")
    args = parser.parse_args()
    parametros = Parametros(
        dias=args.dias,
        wp=args.wp,
        semilla=args.semilla,
        tipo_bateria=args.tipo_bateria,
        capacidad_wh=args.capacidad_wh,
        perfil=args.perfil,
        acciones_manuales=not args.sin_acciones_manuales,
    )
    try:
        from sqlalchemy import text

        from database import SessionLocal

        with SessionLocal() as db:
            if args.solo_calcular and db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION READ ONLY"))
            escrito = ejecutar(db, args.dueno_correo, parametros, args.domo_id, args.solo_calcular, args.reiniciar)
    except Detenido as exc:
        sys.exit(f"Detenido: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nCancelado. No se escribió nada.")
    except Exception as exc:
        # Solo el tipo: el texto de un error de conexión puede traer la URL con la clave.
        sys.exit(f"No se pudo completar ({type(exc).__name__}); no se escribió nada. Revisa la conexión y que alembic_v2 esté en head.")
    sys.exit(0 if escrito or args.solo_calcular else 1)


if __name__ == "__main__":
    main()
