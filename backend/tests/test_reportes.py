"""Reportes y exportación a Power BI, con datos inventados de resultado conocido.

`escenario` se usa también contra Postgres en test_migracion_postgres.py, porque allí
las consultas de fechas son otras."""

import csv
import importlib.util
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import ACTOR_REF_R13, Accion, Alerta, Domo
from servicios.mediciones import guardar_medicion
from servicios.reportes import limites_utc, resumen_del_domo
from tests.base import PruebaApi

BOGOTA = timezone(timedelta(hours=-5))
DIA_1 = date(2026, 3, 10)
DIA_2 = date(2026, 3, 11)


def local(dia, hora, minuto=0):
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=BOGOTA).astimezone(timezone.utc)


def medir(db, domo_id, tema, valores, instante):
    """Una lectura en `mediciones`, como la guarda el listener."""
    guardar_medicion(db, domo_id, tema, valores, instante)


def accion_de(dispositivo_id, actor, accion, instante, correo="dueno@prueba.co"):
    """Una fila de `acciones`. `actor` con los nombres de v1: 'sistema', 'dueño' o 'administrador'."""
    actor_tipo = {"dueño": "dueno"}.get(actor, actor)
    return Accion(
        dispositivo_id=dispositivo_id,
        accion=accion,
        estado_anterior="encendido" if accion == "apagar" else "apagado",
        estado_nuevo="apagado" if accion == "apagar" else "encendido",
        actor_tipo=actor_tipo,
        actor_ref=ACTOR_REF_R13 if actor_tipo == "sistema" else correo,
        resultado="ok",
        fecha_hora=instante,
    )


def escenario(db, domo_id):
    """Dos días de Bogotá, con la medianoche entre las 04:59 y las 05:00 UTC.

    Generación: 23:40 y 23:50 del día 1 a 120 W (10 min = 20 Wh, día 1); 00:00 y 00:10
    del día 2 a 120 W (20 Wh cada tramo, día 2: el tramo que cruza la medianoche cae en
    el día en que termina); hueco de 50 min hasta las 01:00, que no se integra; y 01:00 a
    01:10 subiendo de 60 a 180 W (promedio 120 W, 20 Wh). Total: 20 + 40 + 20 = 80 Wh.
    Consumo: 23:50 a 00:00 a 60 W (10 Wh, día 2).
    SoC con batería AGM (reserva 50): 60 y 40 el día 1 a las 23:40 y 23:50; 40, 45 y 70
    el día 2 a las 00:00, 00:10 y 00:20. Bajo la reserva: 23:50-00:00 y 00:00-00:10 y
    00:10-00:20, 30 min = 0,5 h.
    """
    domo = db.get(Domo, domo_id)
    domo.bateria_tipo, domo.bateria_capacidad_wh = "agm", 1200
    for instante, potencia in (
        (local(DIA_1, 23, 40), 120),
        (local(DIA_1, 23, 50), 120),
        (local(DIA_2, 0, 0), 120),
        (local(DIA_2, 0, 10), 120),
        (local(DIA_2, 1, 0), 60),
        (local(DIA_2, 1, 10), 180),
    ):
        medir(db, domo_id, "fotovoltaica", {"potencia_generada": potencia}, instante)
    for instante in (local(DIA_1, 23, 50), local(DIA_2, 0, 0)):
        medir(db, domo_id, "consumo", {"potencia_consumida": 60}, instante)
    for instante, soc in (
        (local(DIA_1, 23, 40), 60),
        (local(DIA_1, 23, 50), 40),
        (local(DIA_2, 0, 0), 40),
        (local(DIA_2, 0, 10), 45),
        (local(DIA_2, 0, 20), 70),
    ):
        medir(db, domo_id, "bateria", {"estado_carga_soc": soc}, instante)
    db.commit()


class Comprobaciones:
    def comprobar_escenario(self, r):
        self.assertAlmostEqual(r["kwh_generados"], 0.080, places=3)
        self.assertAlmostEqual(r["kwh_consumidos"], 0.010, places=3)
        self.assertAlmostEqual(r["balance_kwh"], 0.070, places=3)
        self.assertAlmostEqual(r["horas_bajo_reserva"], 0.5, places=3)
        self.assertEqual(r["reserva_soc"], 50)

        dia1, dia2 = r["dias"]
        self.assertEqual((dia1["fecha"], dia2["fecha"]), ("2026-03-10", "2026-03-11"))
        self.assertAlmostEqual(dia1["kwh_generados"], 0.020, places=3)
        self.assertAlmostEqual(dia2["kwh_generados"], 0.060, places=3)
        self.assertEqual(dia1["kwh_consumidos"], 0)
        self.assertAlmostEqual(dia2["kwh_consumidos"], 0.010, places=3)
        self.assertEqual((dia1["soc_minimo"], dia1["soc_maximo"]), (40, 60))
        self.assertEqual((dia2["soc_minimo"], dia2["soc_maximo"]), (40, 70))
        self.assertAlmostEqual(dia1["soc_promedio"], 50, places=3)
        self.assertAlmostEqual(dia2["soc_promedio"], 155 / 3, places=3)

        perfil = {h["hora"]: h for h in r["perfil_horario"]}
        self.assertEqual(len(perfil), 24)
        self.assertAlmostEqual(perfil[23]["generacion_w"], 120, places=3)
        self.assertAlmostEqual(perfil[0]["generacion_w"], 120, places=3)
        self.assertAlmostEqual(perfil[1]["generacion_w"], 120, places=3)
        self.assertIsNone(perfil[12]["generacion_w"])
        self.assertAlmostEqual(perfil[23]["consumo_w"], 60, places=3)


class Limites(PruebaApi):
    def test_los_dias_son_de_bogota(self):
        inicio, fin = limites_utc(DIA_1, DIA_1)
        self.assertEqual(inicio, datetime(2026, 3, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(fin, datetime(2026, 3, 11, 5, tzinfo=timezone.utc))


class Energia(PruebaApi, Comprobaciones):
    def get(self, desde=DIA_1, hasta=DIA_2, usuario=None):
        return self.client.get(self.ruta("/reportes/resumen"), params={"desde": desde, "hasta": hasta}, headers=self.auth(usuario))

    def test_escenario_conocido(self):
        escenario(self.db, self.domo_a.id)
        respuesta = self.get()
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.comprobar_escenario(respuesta.json())

    def test_el_rango_de_un_dia_no_incluye_lo_del_otro(self):
        escenario(self.db, self.domo_a.id)
        r = self.get(DIA_1, DIA_1).json()
        self.assertEqual(len(r["dias"]), 1)
        self.assertAlmostEqual(r["kwh_generados"], 0.020, places=3)
        r = self.get(DIA_2, DIA_2).json()
        # Sin la lectura de las 23:50, el tramo que cruza la medianoche no se integra.
        self.assertAlmostEqual(r["kwh_generados"], 0.040, places=3)

    def test_sin_limite_de_lecturas(self):
        for minuto in range(1500):
            medir(self.db, self.domo_a.id, "fotovoltaica", {"potencia_generada": 60}, local(DIA_1, 0) + timedelta(minutes=minuto / 2))
        self.db.commit()
        # 1500 lecturas cada 30 s: 749,5 min a 60 W.
        self.assertAlmostEqual(self.get(DIA_1, DIA_1).json()["kwh_generados"], 0.7495, places=3)

    def test_sin_datos(self):
        r = self.get().json()
        self.assertEqual((r["kwh_generados"], r["horas_bajo_reserva"]), (0, 0))
        self.assertIsNone(r["dias"][0]["kwh_generados"])
        self.assertIsNone(r["dias"][0]["soc_minimo"])
        self.assertEqual(r["tipo_bateria"], "lifepo4")

    def test_una_lectura_sin_dato_no_parte_el_tramo(self):
        for minuto, potencia in ((0, 60), (5, None), (10, 60)):
            medir(self.db, self.domo_a.id, "fotovoltaica", {"potencia_generada": potencia}, local(DIA_1, 8, minuto))
        self.db.commit()
        self.assertAlmostEqual(self.get(DIA_1, DIA_1).json()["kwh_generados"], 0.010, places=3)

    def test_otro_domo_no_cuenta(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        escenario(self.db, otro.id)
        self.assertEqual(self.get().json()["kwh_generados"], 0)

    def test_acceso(self):
        ajeno = self.usuario("ajeno@prueba.co", "dueño")
        self.db.commit()
        self.assertEqual(self.get(usuario=self.admin).status_code, 200)
        self.assertEqual(self.get(usuario=ajeno).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/reportes/resumen"), params={"desde": DIA_1, "hasta": DIA_2}).status_code, 401)

    def test_rangos_invalidos(self):
        self.assertEqual(self.get(DIA_2, DIA_1).status_code, 422)
        self.assertEqual(self.get(DIA_1, DIA_1 + timedelta(days=366)).status_code, 422)
        self.assertEqual(self.get(DIA_1, DIA_1 + timedelta(days=365)).status_code, 200)
        self.assertEqual(self.client.get(self.ruta("/reportes/resumen"), params={"desde": "ayer", "hasta": DIA_2}, headers=self.auth()).status_code, 422)
        self.assertEqual(self.client.get(self.ruta("/reportes/resumen"), headers=self.auth()).status_code, 422)


class CortesYAcciones(PruebaApi):
    def accion(self, toma, rol, accion, instante):
        self.db.add(accion_de(toma.id, rol, accion, instante))
        self.db.commit()

    def resumen(self, desde=DIA_1, hasta=DIA_2, ahora=None):
        self.db.expire_all()
        return resumen_del_domo(self.db, self.domo_a.id, desde, hasta, ahora or local(DIA_2, 23, 59))

    def test_cortes_y_tiempo_apagada(self):
        confort = self.toma("c")
        prescindible = self.toma("p", categoria="prescindible")
        self.accion(confort, "sistema", "apagar", local(DIA_1, 10))
        self.accion(confort, "sistema", "encender", local(DIA_1, 12))
        self.accion(confort, "sistema", "apagar", local(DIA_1, 20))
        self.accion(confort, "dueño", "encender", local(DIA_1, 21))
        self.accion(prescindible, "sistema", "apagar", local(DIA_1, 9))
        self.accion(prescindible, "sistema", "encender", local(DIA_1, 9, 30))

        r = self.resumen()
        categorias = {c["categoria"]: c["cortes"] for c in r["cortes_por_categoria"]}
        self.assertEqual(categorias, {"prescindible": 1, "confort": 2, "importante": 0})
        tomas = r["cortes_por_toma"]
        self.assertEqual([(t["nombre"], t["cortes"]) for t in tomas], [("c", 2), ("p", 1)])
        self.assertAlmostEqual(tomas[0]["horas_apagada"], 3.0, places=3)
        self.assertAlmostEqual(tomas[1]["horas_apagada"], 0.5, places=3)

    def test_corte_sin_cerrar_cuenta_hasta_ahora(self):
        toma = self.toma("t")
        self.accion(toma, "sistema", "apagar", local(DIA_2, 10))
        r = self.resumen(ahora=local(DIA_2, 13))
        self.assertAlmostEqual(r["cortes_por_toma"][0]["horas_apagada"], 3.0, places=3)

    def test_corte_que_empezo_antes_se_recorta_y_no_se_cuenta_de_nuevo(self):
        toma = self.toma("t")
        self.accion(toma, "sistema", "apagar", local(DIA_1, 22))
        self.accion(toma, "sistema", "encender", local(DIA_2, 2))
        r = self.resumen(DIA_2, DIA_2)
        self.assertEqual(r["cortes_por_toma"][0]["cortes"], 0)
        self.assertAlmostEqual(r["cortes_por_toma"][0]["horas_apagada"], 2.0, places=3)
        self.assertEqual(sum(c["cortes"] for c in r["cortes_por_categoria"]), 0)

    def test_corte_que_termina_despues_se_recorta(self):
        toma = self.toma("t")
        self.accion(toma, "sistema", "apagar", local(DIA_1, 22))
        self.accion(toma, "sistema", "encender", local(DIA_2, 2))
        r = self.resumen(DIA_1, DIA_1, ahora=local(DIA_2, 12))
        self.assertAlmostEqual(r["cortes_por_toma"][0]["horas_apagada"], 2.0, places=3)

    def test_corte_ya_terminado_antes_del_rango_no_aparece(self):
        toma = self.toma("t")
        self.accion(toma, "sistema", "apagar", local(DIA_1, 8))
        self.accion(toma, "sistema", "encender", local(DIA_1, 9))
        self.assertEqual(self.resumen(DIA_2, DIA_2)["cortes_por_toma"], [])

    def test_acciones_manuales_y_automaticas(self):
        toma = self.toma("t")
        self.accion(toma, "sistema", "apagar", local(DIA_1, 8))
        self.accion(toma, "sistema", "encender", local(DIA_1, 9))
        self.accion(toma, "dueño", "apagar", local(DIA_1, 10))
        self.accion(toma, "administrador", "encender", local(DIA_1, 11))
        self.accion(toma, "administrador", "apagar", local(DIA_1, 12))
        a = self.resumen()["acciones"]
        self.assertEqual((a["automaticas"], a["manuales"]), (2, 3))
        self.assertEqual({r["rol"]: r["total"] for r in a["por_rol"]}, {"sistema": 2, "administrador": 2, "dueño": 1})


class Alertas(PruebaApi):
    def crear_alerta(self, tipo="bateria_baja", nivel="advertencia", instante=None, atendida=None):
        alerta = Alerta(domo_id=self.domo_a.id, tipo_alerta=tipo, nivel_prioridad=nivel, descripcion="x", fecha_hora=instante or local(DIA_1, 8), fecha_atendida=atendida, estado_alerta="atendida" if atendida else "activa")
        self.db.add(alerta)
        self.db.commit()
        return alerta

    def alertas(self):
        return resumen_del_domo(self.db, self.domo_a.id, DIA_1, DIA_2, local(DIA_2, 23))["alertas"]

    def test_por_tipo_nivel_y_tiempo_medio(self):
        self.crear_alerta(atendida=local(DIA_1, 8, 10))
        self.crear_alerta(instante=local(DIA_1, 9), atendida=local(DIA_1, 9, 30))
        self.crear_alerta("sobrecarga", "critico")
        a = self.alertas()
        self.assertEqual(a["total"], 3)
        self.assertEqual({(t["tipo_alerta"], t["nivel_prioridad"]): t["total"] for t in a["por_tipo"]}, {("bateria_baja", "advertencia"): 2, ("sobrecarga", "critico"): 1})
        self.assertAlmostEqual(a["minutos_medios_hasta_atendida"], 20, places=2)
        self.assertEqual(a["alertas_con_tiempo"], 2)

    def test_sin_atender_no_hay_promedio(self):
        self.crear_alerta()
        self.assertIsNone(self.alertas()["minutos_medios_hasta_atendida"])

    def test_atender_por_la_api_guarda_la_fecha(self):
        alerta = self.crear_alerta()
        ruta = self.ruta(f"/alertas/{alerta.id}")
        datos = self.client.patch(ruta, json={"estado_alerta": "atendida"}, headers=self.auth()).json()
        self.assertIsNotNone(datos["fecha_atendida"])
        primera = datos["fecha_atendida"]
        self.assertEqual(self.client.patch(ruta, json={"estado_alerta": "cerrada"}, headers=self.auth()).json()["fecha_atendida"], primera)
        self.assertIsNone(self.client.patch(ruta, json={"estado_alerta": "activa"}, headers=self.auth()).json()["fecha_atendida"])

    def test_r13_tambien_la_guarda(self):
        self.toma("c")
        self.soc(30)
        self.evaluar()
        self.soc(90)
        self.evaluar()
        alerta = self.alertas_bateria()[0]
        self.assertEqual(alerta.estado_alerta, "atendida")
        self.assertIsNotNone(alerta.fecha_atendida)


def _cargar_exportador():
    ruta = Path(__file__).resolve().parent.parent / "tools" / "exportar_bi.py"
    spec = importlib.util.spec_from_file_location("exportar_bi", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


exportador = _cargar_exportador()


def leer(carpeta, nombre):
    with open(carpeta / nombre, encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


class ComprobacionesExportacion:
    def comprobar_exportacion(self, db, domo_id):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "salida"
            exportador.exportar(db, carpeta, [domo_id], DIA_1, DIA_2)
            nombres = {p.name for p in carpeta.iterdir()}
            self.assertEqual(
                nombres,
                {"hechos_lecturas_5min.csv", "hechos_acciones.csv", "hechos_alertas.csv", "hechos_cortes.csv", "dim_fecha.csv", "dim_hora.csv", "dim_dispositivo.csv", "dim_domo.csv"},
            )
            self.assertTrue((carpeta / "dim_domo.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
            lecturas = leer(carpeta, "hechos_lecturas_5min.csv")
            # La misma energía que el reporte: 80 Wh, 10 Wh y 30 min bajo la reserva.
            self.assertAlmostEqual(sum(float(f["generacion_wh"] or 0) for f in lecturas), 80, places=2)
            self.assertAlmostEqual(sum(float(f["consumo_wh"] or 0) for f in lecturas), 10, places=2)
            self.assertAlmostEqual(sum(float(f["minutos_bajo_reserva"] or 0) for f in lecturas), 30, places=1)
            primera = lecturas[0]
            self.assertEqual((primera["fecha_id"], primera["hora"], primera["inicio_local"]), ("20260310", "23", "2026-03-10 23:40"))
            self.assertEqual(lecturas[2]["fecha_id"], "20260311")
            self.assertEqual([f["fecha_id"] for f in leer(carpeta, "dim_fecha.csv")], ["20260310", "20260311"])
            self.assertEqual(len(leer(carpeta, "dim_hora.csv")), 24)


class Exportacion(PruebaApi, ComprobacionesExportacion):
    def test_mismos_totales_que_el_reporte(self):
        escenario(self.db, self.domo_a.id)
        self.comprobar_exportacion(self.db, self.domo_a.id)

    def test_acciones_y_alertas_sin_datos_personales(self):
        self.dueno.telefono = "3001234567"
        toma = self.toma("Toma ñandú", ubicacion="Cocina")
        self.db.add(accion_de(toma.id, "dueño", "apagar", local(DIA_1, 8)))
        self.db.add(accion_de(toma.id, "sistema", "apagar", local(DIA_1, 9)))
        self.db.add(Alerta(domo_id=self.domo_a.id, tipo_alerta="bateria_baja", nivel_prioridad="critico", descripcion="x", fecha_hora=local(DIA_1, 8), fecha_atendida=local(DIA_1, 8, 15), estado_alerta="atendida"))
        self.db.commit()
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            exportador.exportar(self.db, carpeta, [self.domo_a.id], DIA_1, DIA_1)
            acciones = leer(carpeta, "hechos_acciones.csv")
            self.assertEqual([(a["rol"], a["automatica"]) for a in acciones], [("dueño", "0"), ("sistema", "1")])
            self.assertNotIn("usuario_id", acciones[0])
            self.assertEqual(leer(carpeta, "hechos_alertas.csv")[0]["minutos_hasta_atendida"], "15.0")
            dispositivo = leer(carpeta, "dim_dispositivo.csv")[0]
            self.assertEqual((dispositivo["nombre"], dispositivo["categoria"], dispositivo["real"]), ("Toma ñandú", "confort", "0"))
            todo = "".join(p.read_text(encoding="utf-8-sig") for p in carpeta.iterdir())
            for prohibido in ("dueno@prueba.co", "admin@prueba.co", "3001234567", "contrasena", "hash", "token"):
                self.assertNotIn(prohibido, todo)

    def test_el_codigo_no_toca_la_tabla_usuarios(self):
        fuente = (Path(__file__).resolve().parent.parent / "tools" / "exportar_bi.py").read_text(encoding="utf-8")
        for prohibido in ("Usuario", "usuarios", "contrasena", "usuario_id"):
            self.assertNotIn(prohibido, fuente)

    def test_domo_inexistente(self):
        with tempfile.TemporaryDirectory() as temporal, self.assertRaises(LookupError):
            exportador.exportar(self.db, Path(temporal), [999])

    def test_sin_fechas_exporta_todo_y_calendario_del_dato(self):
        escenario(self.db, self.domo_a.id)
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            exportador.exportar(self.db, carpeta)
            self.assertEqual(len(leer(carpeta, "dim_fecha.csv")), 2)
            self.assertEqual(len(leer(carpeta, "dim_domo.csv")), 1)

    def test_error_de_conexion_no_muestra_la_url(self):
        clave = "postgresql://usuario:clave-secreta@host/base"
        with patch("sys.argv", ["exportar_bi.py"]), patch.dict("os.environ", {"USERPROFILE": tempfile.gettempdir()}), patch.object(exportador, "exportar", side_effect=RuntimeError(clave)):
            with self.assertRaises(SystemExit) as salida:
                exportador.main()
        self.assertNotIn("clave-secreta", str(salida.exception))
        self.assertIn("RuntimeError", str(salida.exception))


