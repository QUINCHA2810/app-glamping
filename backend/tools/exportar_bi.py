"""
Exporta los datos de Vatia a CSV para Power BI, en un modelo estrella. SOLO LECTURA: la
transacción se abre como READ ONLY, así que la base rechazaría cualquier escritura.

Usa la base de database.py (DATABASE_URL_V2 en esta rama) sin imprimir la URL. Nunca
lee las cuentas ni exporta hashes: en las acciones basta el rol. Ver docs/PowerBI.md.

Modelo v2: lee `mediciones`, `acciones` (el domo sale por JOIN con dispositivos) y la
batería y el perfil de `domos`. Las columnas de los CSV son las mismas de v1; los
umbrales de dim_dispositivo son los calculados con la categoría y el perfil.

    python tools/exportar_bi.py
    python tools/exportar_bi.py --domo 1 --desde 2026-11-01 --hasta 2026-11-30

Escribe en %USERPROFILE%\\vatia_bi\\AAAAMMDD_HHMM (fuera del repo y de OneDrive), en
UTF-8 con BOM, separados por coma y con punto decimal:

    hechos_lecturas_5min.csv  generación, consumo y SoC cada 5 minutos
    hechos_acciones.csv       acciones sobre dispositivos
    hechos_alertas.csv        alertas
    hechos_cortes.csv         cada corte de R-13, con su duración
    dim_fecha.csv, dim_hora.csv, dim_dispositivo.csv, dim_domo.csv

Las fechas son de Bogotá y ambas se incluyen. La energía se calcula con las mismas
reglas de servicios/reportes.py: un hueco de más de 10 minutos no se integra.
"""

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import Integer, cast, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

CUBETA_S = 300
# dim_domo.origen_datos: un domo de demostración tiene solo datos sintéticos.
ORIGEN_SINTETICO = "Demostración · datos sintéticos"
# Un domo del proyecto también puede tener lecturas del simulador: ver docs/PowerBI.md.
ORIGEN_REAL = "Domo del proyecto"
FRANJAS = ((0, "Madrugada"), (6, "Mañana"), (12, "Tarde"), (18, "Noche"))
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _fecha_id(dia: date) -> int:
    return dia.year * 10000 + dia.month * 100 + dia.day


def _a_utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor


def _epoca(db, columna):
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import extract

        return extract("epoch", columna)
    return cast(func.strftime("%s", columna), Integer)


def _redondo(valor, decimales=3):
    return round(float(valor), decimales) if valor is not None else None


def _escribir(carpeta: Path, nombre: str, columnas: list[str], filas) -> int:
    total = 0
    with open(carpeta / nombre, "w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(columnas)
        for fila in filas:
            escritor.writerow(["" if v is None else v for v in fila])
            total += 1
    return total


def _lecturas_de_domo(db, domo_id, inicio, fin, reserva) -> dict[int, dict]:
    """Por cubeta de 5 minutos: promedios de cada medida, y la energía y el tiempo bajo
    la reserva integrados en esa cubeta."""
    from models import Medicion
    from servicios.reportes import intervalos, segundos_bajo_reserva, wh_integrados

    cubetas: dict[int, dict] = {}

    def medir(modelo, campo, prefijo, extra=None):
        tramos = intervalos(db, modelo, campo, domo_id, inicio, fin)
        cubeta = (cast(_epoca(db, tramos.c.t), Integer) / CUBETA_S).label("cubeta")
        columnas = [cubeta, func.avg(tramos.c.v).label("promedio"), func.sum(wh_integrados(db, tramos)).label("integral")]
        if extra is not None:
            columnas.append(func.sum(extra(tramos)).label("extra"))
        for fila in db.execute(select(*columnas).group_by(cubeta)):
            dato = cubetas.setdefault(int(fila.cubeta), {})
            dato[f"{prefijo}_prom"] = float(fila.promedio)
            dato[f"{prefijo}_integral"] = float(fila.integral or 0)
            if extra is not None:
                dato["segundos_bajo_reserva"] = float(fila.extra or 0)

    medir(Medicion, "potencia_fv_w", "generacion")
    medir(Medicion, "potencia_consumo_w", "consumo")
    medir(Medicion, "soc_pct", "soc", lambda t: segundos_bajo_reserva(db, t, reserva))
    return cubetas


def exportar(db: Session, carpeta: Path, domos_pedidos=None, desde=None, hasta=None) -> dict[str, int]:
    from models import ROL_API, Accion, Alerta, Dispositivo, Domo
    from servicios.categorias import RESERVA_SOC_POR_BATERIA, perfil_de, tipo_bateria_de, umbrales_de
    from servicios.reportes import ZONA_BOGOTA, intervalos_de_corte, limites_utc

    hoy = datetime.now(ZONA_BOGOTA).date()
    inicio = limites_utc(desde, desde)[0] if desde else None
    fin = limites_utc(hasta, hasta)[1] if hasta else None

    consulta_domos = db.query(Domo)
    if domos_pedidos:
        consulta_domos = consulta_domos.filter(Domo.id.in_(domos_pedidos))
    domos = consulta_domos.order_by(Domo.id).all()
    if domos_pedidos and {d.id for d in domos} != set(domos_pedidos):
        raise LookupError("Alguno de los domos pedidos no existe.")
    ids = [d.id for d in domos]
    # Después de la primera consulta: si la conexión falla, no queda una carpeta vacía.
    carpeta.mkdir(parents=True, exist_ok=True)

    fechas: set[date] = set()
    conteos: dict[str, int] = {}

    def desglose(instante: datetime):
        local = _a_utc(instante).astimezone(ZONA_BOGOTA)
        fechas.add(local.date())
        return local, _fecha_id(local.date()), local.hour

    # ---- dimensiones de domo y dispositivo
    filas_domo = []
    reservas = {}
    ajustes = {}
    for domo in domos:
        tipo = tipo_bateria_de(domo)
        ajustes[domo.id] = (tipo, perfil_de(domo))
        reservas[domo.id] = RESERVA_SOC_POR_BATERIA[tipo]
        filas_domo.append(
            (
                domo.id,
                domo.nombre,
                domo.ubicacion,
                tipo,
                domo.bateria_capacidad_wh,
                reservas[domo.id],
                perfil_de(domo),
                int(domo.es_demostracion),
                ORIGEN_SINTETICO if domo.es_demostracion else ORIGEN_REAL,
            )
        )
    conteos["dim_domo.csv"] = _escribir(
        carpeta,
        "dim_domo.csv",
        ["domo_id", "nombre", "ubicacion", "tipo_bateria", "capacidad_wh", "reserva_soc", "perfil_control", "es_demostracion", "origen_datos"],
        filas_domo,
    )

    dispositivos = db.query(Dispositivo).filter(Dispositivo.domo_id.in_(ids)).order_by(Dispositivo.id).all()
    conteos["dim_dispositivo.csv"] = _escribir(
        carpeta,
        "dim_dispositivo.csv",
        ["dispositivo_id", "domo_id", "nombre", "tipo", "categoria", "ubicacion", "activo", "desactivado_en_local", "real", "umbral_apagado_soc", "umbral_reactivacion_soc"],
        (
            (
                d.id,
                d.domo_id,
                d.nombre,
                d.tipo,
                d.categoria,
                d.ubicacion,
                int(d.activo),
                _a_utc(d.desactivado_en).astimezone(ZONA_BOGOTA).strftime("%Y-%m-%d %H:%M:%S") if d.desactivado_en else None,
                int(d.id_fisico is not None),
                *(_redondo(u, 1) for u in umbrales_de(d.categoria, *ajustes[d.domo_id])),
            )
            for d in dispositivos
        ),
    )

    # ---- hechos: lecturas cada 5 minutos
    filas_lecturas = []
    for domo_id in ids:
        for cubeta, dato in sorted(_lecturas_de_domo(db, domo_id, inicio, fin, reservas[domo_id]).items()):
            local, fecha_id, hora = desglose(datetime.fromtimestamp(cubeta * CUBETA_S, timezone.utc))
            filas_lecturas.append(
                (
                    domo_id,
                    fecha_id,
                    hora,
                    local.strftime("%Y-%m-%d %H:%M"),
                    _redondo(dato.get("generacion_prom")),
                    _redondo(dato.get("consumo_prom")),
                    _redondo(dato.get("soc_prom")),
                    _redondo(dato.get("generacion_integral", 0.0), 4),
                    _redondo(dato.get("consumo_integral", 0.0), 4),
                    _redondo(dato.get("segundos_bajo_reserva", 0.0) / 60.0),
                )
            )
    conteos["hechos_lecturas_5min.csv"] = _escribir(
        carpeta,
        "hechos_lecturas_5min.csv",
        ["domo_id", "fecha_id", "hora", "inicio_local", "generacion_w", "consumo_w", "soc", "generacion_wh", "consumo_wh", "minutos_bajo_reserva"],
        filas_lecturas,
    )

    # ---- hechos: acciones (basta el rol)
    consulta = (
        select(
            Accion.id,
            Dispositivo.domo_id,
            Accion.dispositivo_id,
            Accion.actor_tipo,
            Accion.accion,
            Accion.resultado,
            Accion.fecha_hora,
        )
        .join(Dispositivo, Dispositivo.id == Accion.dispositivo_id)
        .where(Dispositivo.domo_id.in_(ids))
    )
    if inicio:
        consulta = consulta.where(Accion.fecha_hora >= inicio)
    if fin:
        consulta = consulta.where(Accion.fecha_hora < fin)
    filas_acciones = []
    for f in db.execute(consulta.order_by(Accion.fecha_hora, Accion.id)):
        local, fecha_id, hora = desglose(f.fecha_hora)
        rol = ROL_API.get(f.actor_tipo, f.actor_tipo)
        filas_acciones.append(
            (f.id, f.domo_id, f.dispositivo_id, fecha_id, hora, local.strftime("%Y-%m-%d %H:%M:%S"), rol, f.accion, int(f.actor_tipo == "sistema"), f.resultado)
        )
    conteos["hechos_acciones.csv"] = _escribir(
        carpeta,
        "hechos_acciones.csv",
        ["accion_id", "domo_id", "dispositivo_id", "fecha_id", "hora", "momento_local", "rol", "accion", "automatica", "resultado"],
        filas_acciones,
    )

    # ---- hechos: alertas
    consulta = select(
        Alerta.id,
        Alerta.domo_id,
        Alerta.tipo_alerta,
        Alerta.nivel_prioridad,
        Alerta.estado_alerta,
        Alerta.valor_detectado,
        Alerta.valor_limite,
        Alerta.fecha_hora,
        Alerta.fecha_atendida,
    ).where(Alerta.domo_id.in_(ids))
    if inicio:
        consulta = consulta.where(Alerta.fecha_hora >= inicio)
    if fin:
        consulta = consulta.where(Alerta.fecha_hora < fin)
    filas_alertas = []
    for f in db.execute(consulta.order_by(Alerta.fecha_hora, Alerta.id)):
        local, fecha_id, hora = desglose(f.fecha_hora)
        minutos = (_a_utc(f.fecha_atendida) - _a_utc(f.fecha_hora)).total_seconds() / 60.0 if f.fecha_atendida else None
        filas_alertas.append(
            (f.id, f.domo_id, fecha_id, hora, local.strftime("%Y-%m-%d %H:%M:%S"), f.tipo_alerta, f.nivel_prioridad, f.estado_alerta, _redondo(f.valor_detectado), _redondo(f.valor_limite), _redondo(minutos))
        )
    conteos["hechos_alertas.csv"] = _escribir(
        carpeta,
        "hechos_alertas.csv",
        ["alerta_id", "domo_id", "fecha_id", "hora", "momento_local", "tipo_alerta", "nivel_prioridad", "estado_alerta", "valor_detectado", "valor_limite", "minutos_hasta_atendida"],
        filas_alertas,
    )

    # ---- hechos: cortes de R-13, con las mismas reglas que el reporte
    ahora = datetime.now(timezone.utc)
    filas_cortes = []
    for domo_id in ids:
        for corte in intervalos_de_corte(db, domo_id, inicio, fin, ahora):
            local, fecha_id, hora = desglose(corte["inicio"])
            filas_cortes.append(
                (
                    domo_id,
                    corte["dispositivo_id"],
                    fecha_id,
                    hora,
                    local.strftime("%Y-%m-%d %H:%M:%S"),
                    _a_utc(corte["fin"]).astimezone(ZONA_BOGOTA).strftime("%Y-%m-%d %H:%M:%S"),
                    _redondo(corte["horas"], 4),
                    int(corte["empieza_en_el_rango"]),
                )
            )
    conteos["hechos_cortes.csv"] = _escribir(
        carpeta,
        "hechos_cortes.csv",
        ["domo_id", "dispositivo_id", "fecha_id", "hora", "inicio_local", "fin_local", "horas_apagada", "empieza_en_el_rango"],
        filas_cortes,
    )

    # ---- dimensiones de tiempo: todo el calendario entre el primer y el último dato
    primero = desde or (min(fechas) if fechas else hoy)
    ultimo = hasta or (max(fechas) if fechas else hoy)
    calendario = []
    dia = primero
    while dia <= ultimo:
        calendario.append((_fecha_id(dia), dia.isoformat(), dia.year, dia.month, MESES[dia.month - 1], dia.day, dia.isoweekday(), DIAS_SEMANA[dia.weekday()]))
        dia += timedelta(days=1)
    conteos["dim_fecha.csv"] = _escribir(
        carpeta, "dim_fecha.csv", ["fecha_id", "fecha", "anio", "mes", "nombre_mes", "dia", "dia_semana_num", "dia_semana"], calendario
    )
    conteos["dim_hora.csv"] = _escribir(
        carpeta,
        "dim_hora.csv",
        ["hora", "etiqueta", "franja"],
        ((h, f"{h:02d}:00", next(nombre for inicio_franja, nombre in reversed(FRANJAS) if h >= inicio_franja)) for h in range(24)),
    )
    return conteos


def _fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{texto!r} no es una fecha AAAA-MM-DD.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta los datos de Vatia a CSV para Power BI (solo lectura).")
    parser.add_argument("--domo", type=int, action="append", help="domo a exportar; se puede repetir. Por defecto, todos")
    parser.add_argument("--desde", type=_fecha, help="fecha inicial AAAA-MM-DD, en hora de Bogotá")
    parser.add_argument("--hasta", type=_fecha, help="fecha final AAAA-MM-DD, incluida")
    args = parser.parse_args()
    if args.desde and args.hasta and args.desde > args.hasta:
        parser.error("--desde no puede ser posterior a --hasta.")

    perfil = os.environ.get("USERPROFILE") or str(Path.home())
    carpeta = Path(perfil) / "vatia_bi" / datetime.now().strftime("%Y%m%d_%H%M")

    try:
        from database import engine

        with Session(engine) as db:
            if engine.dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION READ ONLY"))
            conteos = exportar(db, carpeta, args.domo, args.desde, args.hasta)
            db.rollback()
    except LookupError as exc:
        sys.exit(str(exc))
    except Exception as exc:
        # Solo el tipo: el texto de un error de conexión puede traer la URL con la clave.
        sys.exit(f"No se pudo exportar ({type(exc).__name__}). Revisa la conexión y que las migraciones estén aplicadas.")

    print(f"Exportado en {carpeta}")
    for nombre, total in conteos.items():
        print(f"  {nombre}: {total} filas")


if __name__ == "__main__":
    main()
