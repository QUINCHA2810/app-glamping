"""Clima de referencia (servicios/clima.py, routers/clima.py, alembic_v2 5a1c7e3f9b24):
lectura de la respuesta de Open-Meteo, fallos de red, sin duplicados, domos sin
coordenadas, el hilo que no muere y los permisos de la API. Sin red: urlopen va
parcheado."""

import io
import json
import socket
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models import RANGOS_CLIMA, Clima
from servicios import clima
from tests.base import PruebaApi, modulo_migracion_v2

# Forma real de la respuesta (timezone=America/Bogota: hora local sin zona + desfase).
RESPUESTA = {
    "latitude": 4.625,
    "longitude": -74.0625,
    "utc_offset_seconds": -18000,
    "timezone": "America/Bogota",
    "current_units": {"time": "iso8601", "interval": "seconds", "temperature_2m": "°C"},
    "current": {
        "time": "2026-11-22T14:15",
        "interval": 900,
        "temperature_2m": 17.3,
        "cloud_cover": 75,
        "shortwave_radiation": 412.0,
        "precipitation": 0.2,
        "weather_code": 3,
        "is_day": 1,
    },
}
FECHA_UTC = datetime(2026, 11, 22, 19, 15, tzinfo=timezone.utc)
LAT, LON = 4.638158, -74.064359


def respuesta(**cambios) -> dict:
    copia = json.loads(json.dumps(RESPUESTA))
    copia["current"].update(cambios)
    return copia


class _Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def urlopen_con(cuerpo: bytes):
    return patch("servicios.clima.urllib.request.urlopen", side_effect=lambda *a, **k: _Respuesta(cuerpo))


class Parseo(TestCase):
    def test_respuesta_real_en_utc(self):
        self.assertEqual(clima.parsear(RESPUESTA), {
            "fecha_hora": FECHA_UTC,
            "temperatura_c": 17.3,
            "nubosidad_pct": 75.0,
            "radiacion_w_m2": 412.0,
            "precipitacion_mm": 0.2,
            "codigo_clima": 3,
            "es_de_dia": True,
        })

    def test_de_noche(self):
        datos = clima.parsear(respuesta(is_day=0, shortwave_radiation=0))
        self.assertEqual((datos["es_de_dia"], datos["radiacion_w_m2"]), (False, 0.0))

    def test_valores_que_faltan_o_absurdos_quedan_en_none(self):
        datos = clima.parsear(respuesta(
            temperature_2m=None, cloud_cover=140, shortwave_radiation=-5, precipitation="0.1",
            weather_code=3.5, is_day=2,
        ))
        self.assertEqual(datos["fecha_hora"], FECHA_UTC)
        for columna in ("temperatura_c", "nubosidad_pct", "radiacion_w_m2", "precipitacion_mm", "codigo_clima", "es_de_dia"):
            self.assertIsNone(datos[columna], columna)
        sin_variables = {"utc_offset_seconds": -18000, "current": {"time": "2026-11-22T14:15"}}
        self.assertIsNone(clima.parsear(sin_variables)["radiacion_w_m2"])

    def test_booleanos_e_infinitos_no_son_numeros(self):
        datos = clima.parsear(respuesta(cloud_cover=True, temperature_2m=float("inf")))
        self.assertIsNone(datos["nubosidad_pct"])
        self.assertIsNone(datos["temperatura_c"])

    def test_sin_current_o_sin_hora_es_error(self):
        for malo in ({}, {"current": None}, {"current": {}}, respuesta(time="ayer"), [1, 2]):
            with self.subTest(malo=malo), self.assertRaises(clima.ErrorClima):
                clima.parsear(malo)
        sin_desfase = respuesta()
        del sin_desfase["utc_offset_seconds"]
        with self.assertRaises(clima.ErrorClima):
            clima.parsear(sin_desfase)

    def test_hora_con_zona_se_respeta(self):
        self.assertEqual(clima.parsear(respuesta(time="2026-11-22T19:15+00:00"))["fecha_hora"], FECHA_UTC)

    def test_url_con_los_parametros_pedidos(self):
        url = clima.url_consulta(LAT, LON)
        self.assertTrue(url.startswith("https://api.open-meteo.com/v1/forecast?"))
        for parte in (
            "latitude=4.638158", "longitude=-74.064359", "timezone=America%2FBogota",
            "current=temperature_2m%2Ccloud_cover%2Cshortwave_radiation%2Cprecipitation%2Cweather_code%2Cis_day",
        ):
            self.assertIn(parte, url)

    def test_rangos_iguales_a_la_migracion(self):
        migracion = modulo_migracion_v2("5a1c7e3f9b24_clima_de_referencia")
        self.assertEqual(migracion.RANGOS_CLIMA, RANGOS_CLIMA)
        self.assertEqual(migracion.down_revision, "9d4f2a7b1c38")
        self.assertIn("ALTER TABLE clima ENABLE ROW LEVEL SECURITY", self._fuente(migracion))
        self.assertIn("CONSTRAINT clima_domo_fecha_key UNIQUE (domo_id, fecha_hora)", migracion.CLIMA)

    @staticmethod
    def _fuente(modulo) -> str:
        with open(modulo.__file__, encoding="utf-8") as f:
            return f.read()


class Red(TestCase):
    def test_respuesta_correcta(self):
        with urlopen_con(json.dumps(RESPUESTA).encode()) as abrir:
            self.assertEqual(clima.consultar(LAT, LON), RESPUESTA)
        peticion = abrir.call_args.args[0]
        self.assertEqual(abrir.call_args.kwargs["timeout"], clima.TIMEOUT_S)
        self.assertIn("latitude=4.638158", peticion.full_url)

    def test_fallos_de_red_son_error_clima(self):
        errores = (
            urllib.error.URLError("sin DNS"),
            urllib.error.HTTPError("u", 500, "error", {}, io.BytesIO()),
            TimeoutError("timed out"),
            socket.timeout("timed out"),
            ConnectionResetError(),
        )
        for error in errores:
            with self.subTest(error=type(error).__name__):
                with patch("servicios.clima.urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(clima.ErrorClima):
                        clima.consultar(LAT, LON)

    def test_json_invalido_o_enorme(self):
        for cuerpo in (b"<html>caido</html>", b"[1,2]", b" " * (clima.TAMANO_MAXIMO + 10)):
            with self.subTest(cuerpo=cuerpo[:20]), urlopen_con(cuerpo), self.assertRaises(clima.ErrorClima):
                clima.consultar(LAT, LON)


class Guardado(PruebaApi):
    def setUp(self):
        super().setUp()
        self.Sesion = sessionmaker(bind=self.db.get_bind(), autoflush=False)
        self.domo_a.latitud, self.domo_a.longitud = LAT, LON
        self.domo_b = self.domo("Domo B", self.dueno, latitud=6.25, longitud=-75.56)
        self.sin_coordenadas = self.domo("Domo C", self.dueno)
        self.db.commit()

    def filas(self):
        self.db.expire_all()
        return self.db.query(Clima).order_by(Clima.domo_id, Clima.fecha_hora).all()

    def test_guarda_un_domo_y_no_duplica(self):
        datos = clima.parsear(RESPUESTA)
        self.assertTrue(clima.guardar(self.db, self.domo_a.id, datos))
        self.assertFalse(clima.guardar(self.db, self.domo_a.id, {**datos, "temperatura_c": 99.0 - 80}))
        self.db.commit()
        filas = self.filas()
        self.assertEqual(len(filas), 1)
        self.assertEqual((filas[0].temperatura_c, filas[0].fuente), (17.3, "open-meteo"))

    def test_el_ciclo_salta_los_domos_sin_coordenadas(self):
        consultas = []

        def consulta(lat, lon):
            consultas.append((lat, lon))
            return RESPUESTA

        resumen = clima.actualizar_todos(self.Sesion, consulta)
        self.assertEqual(consultas, [(LAT, LON), (6.25, -75.56)])
        self.assertEqual(resumen, {"guardados": 2, "repetidos": 0, "fallidos": 0})
        self.assertEqual({f.domo_id for f in self.filas()}, {self.domo_a.id, self.domo_b.id})
        # Otra vuelta en el mismo intervalo de 15 min: la misma fecha_hora, sin duplicados.
        self.assertEqual(clima.actualizar_todos(self.Sesion, consulta), {"guardados": 0, "repetidos": 2, "fallidos": 0})
        self.assertEqual(len(self.filas()), 2)

    def test_un_domo_que_falla_no_detiene_a_los_demas(self):
        def consulta(lat, lon):
            if lat == LAT:
                raise clima.ErrorClima("sin respuesta de Open-Meteo (timed out)")
            return RESPUESTA

        with self.assertLogs("clima", "WARNING") as registro:
            resumen = clima.actualizar_todos(self.Sesion, consulta)
        self.assertEqual(resumen, {"guardados": 1, "repetidos": 0, "fallidos": 1})
        self.assertEqual([f.domo_id for f in self.filas()], [self.domo_b.id])
        self.assertIn(f"domo {self.domo_a.id}", registro.output[0])

    def test_un_error_inesperado_tambien_se_registra_y_sigue(self):
        respuestas = iter([{"current": "no es un objeto"}, RESPUESTA])
        with self.assertLogs("clima", "WARNING"):
            resumen = clima.actualizar_todos(self.Sesion, lambda lat, lon: next(respuestas))
        self.assertEqual(resumen["fallidos"], 1)
        self.assertEqual(resumen["guardados"], 1)

    def test_con_urlopen_simulado_de_punta_a_punta(self):
        with urlopen_con(json.dumps(RESPUESTA).encode()):
            clima.actualizar_todos(self.Sesion, lambda lat, lon: clima.consultar(lat, lon))
        self.assertEqual(len(self.filas()), 2)

    def test_la_base_aplica_los_rangos_y_las_coordenadas(self):
        self.db.add(Clima(domo_id=self.domo_a.id, fecha_hora=FECHA_UTC, nubosidad_pct=101))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.domo_a.latitud = None  # sin longitud no vale
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.domo_b.latitud = 95
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_borrar_el_domo_borra_su_clima(self):
        clima.guardar(self.db, self.domo_b.id, clima.parsear(RESPUESTA))
        self.db.commit()
        self.db.delete(self.domo_b)
        self.db.commit()
        self.assertEqual(self.filas(), [])


class Hilo(TestCase):
    def test_consulta_al_arrancar_y_sobrevive_a_los_fallos(self):
        llamadas = []
        detener = threading.Event()

        def actualizar(fabrica):
            llamadas.append(fabrica)
            if len(llamadas) == 1:
                raise RuntimeError("la base no responde")
            if len(llamadas) == 2:
                raise KeyError("otra cosa")
            detener.set()
            return {}

        with patch("servicios.clima.actualizar_todos", side_effect=actualizar), self.assertLogs("clima", "WARNING") as registro:
            hilo = threading.Thread(target=clima.bucle, args=("fabrica", 0.01, detener))
            hilo.start()
            hilo.join(timeout=5)
        self.assertFalse(hilo.is_alive())
        self.assertEqual(len(llamadas), 3)  # la primera es inmediata; los fallos no lo matan
        self.assertEqual(len(registro.output), 2)

    def test_la_primera_consulta_no_espera_el_intervalo(self):
        hecho = threading.Event()
        with patch("servicios.clima.actualizar_todos", side_effect=lambda f: hecho.set()):
            detener = clima.iniciar_hilo("fabrica", intervalo_s=3600)
            self.assertTrue(hecho.wait(timeout=5))
            detener.set()

    def test_el_listener_no_arranca_el_hilo_con_clima_auto_false(self):
        from tests.base import importar_listener

        listener = importar_listener()
        with patch.object(listener, "CLIMA_AUTO", False), patch("servicios.clima.iniciar_hilo") as iniciar:
            listener._iniciar_clima()
        iniciar.assert_not_called()
        with patch.object(listener, "CLIMA_AUTO", True), patch("servicios.clima.iniciar_hilo", side_effect=RuntimeError):
            with self.assertLogs("mqtt_listener", "WARNING"):
                listener._iniciar_clima()  # no lanza: el listener sigue sin clima


class Api(PruebaApi):
    def setUp(self):
        super().setUp()
        self.domo_a.latitud, self.domo_a.longitud = LAT, LON
        self.db.commit()
        self.intruso = self.usuario("intruso@prueba.co")
        self.otro = self.domo("Domo del intruso", self.intruso)
        self.db.commit()

    def guardar(self, minutos_atras: int, **campos):
        fecha = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=minutos_atras)
        self.db.add(Clima(domo_id=self.domo_a.id, fecha_hora=fecha, **campos))
        self.db.commit()
        return fecha

    def actual(self, usuario=None, domo=None):
        return self.client.get(self.ruta("/clima/actual", domo), headers=self.auth(usuario))

    def serie(self, desde: str, hasta: str, usuario=None):
        return self.client.get(
            self.ruta("/clima", None), params={"fecha_desde": desde, "fecha_hasta": hasta}, headers=self.auth(usuario)
        )

    def test_actual_sin_datos_y_sin_coordenadas(self):
        self.assertEqual(self.actual().json(), {
            "domo_id": self.domo_a.id, "disponible": False, "motivo": "sin_datos", "clima": None, "antiguedad_s": None,
        })
        self.domo_a.latitud = self.domo_a.longitud = None
        self.db.commit()
        respuesta = self.actual()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual((respuesta.json()["disponible"], respuesta.json()["motivo"]), (False, "sin_coordenadas"))

    def test_actual_devuelve_la_ultima_y_su_antiguedad(self):
        self.guardar(40, nubosidad_pct=10)
        self.guardar(10, nubosidad_pct=80, radiacion_w_m2=300, codigo_clima=3, es_de_dia=True)
        cuerpo = self.actual().json()
        self.assertTrue(cuerpo["disponible"])
        self.assertIsNone(cuerpo["motivo"])
        self.assertEqual((cuerpo["clima"]["nubosidad_pct"], cuerpo["clima"]["codigo_clima"]), (80, 3))
        self.assertEqual(cuerpo["clima"]["fuente"], "open-meteo")
        self.assertTrue(cuerpo["clima"]["fecha_hora"].endswith(("Z", "+00:00")), cuerpo["clima"]["fecha_hora"])
        self.assertTrue(600 <= cuerpo["antiguedad_s"] < 700, cuerpo["antiguedad_s"])

    def test_la_serie_en_orden_y_dentro_del_rango(self):
        viejo = self.guardar(120, radiacion_w_m2=100)
        medio = self.guardar(60, radiacion_w_m2=200)
        self.guardar(5, radiacion_w_m2=300)
        desde = (viejo - timedelta(minutes=1)).isoformat()
        hasta = (medio + timedelta(minutes=1)).isoformat()
        respuesta = self.serie(desde, hasta)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual([f["radiacion_w_m2"] for f in respuesta.json()], [100, 200])

    def test_la_serie_valida_el_rango(self):
        self.assertEqual(self.serie("2026-11-22T10:00:00Z", "2026-11-21T10:00:00Z").status_code, 422)
        self.assertEqual(self.serie("2026-10-01T00:00:00Z", "2026-11-22T00:00:00Z").status_code, 422)
        faltante = self.client.get(self.ruta("/clima"), params={"fecha_desde": "2026-11-22T00:00:00Z"}, headers=self.auth())
        self.assertEqual(faltante.status_code, 422)

    def test_permisos_iguales_a_las_mediciones(self):
        self.guardar(5, nubosidad_pct=50)
        # El administrador asignado ve el clima; quien no tiene acceso recibe 404.
        self.assertTrue(self.actual(self.admin).json()["disponible"])
        self.assertEqual(self.actual(self.intruso).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/lecturas-bateria"), headers=self.auth(self.intruso)).status_code, 404)
        self.assertEqual(self.serie("2026-11-01T00:00:00Z", "2026-11-22T00:00:00Z", self.intruso).status_code, 404)
        self.assertEqual(self.actual(domo=self.otro).status_code, 404)
        self.assertEqual(self.client.get(self.ruta("/clima/actual")).status_code, 401)

    def test_domo_out_lleva_las_coordenadas(self):
        domo = self.client.get(self.ruta(""), headers=self.auth()).json()
        self.assertEqual((domo["latitud"], domo["longitud"]), (LAT, LON))
