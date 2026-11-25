"""Lo propio del modelo v2 (ronda DB-2): rol por asignación, acciones por JOIN, alertas
por id_fisico_ref, dispositivos que no se borran, la tabla mediciones (bloques de 10 s,
energía sumada), estado de comunicación, la conexión a DATABASE_URL_V2 y el respaldo
con --origen. Sin Postgres: lo mismo contra Postgres está en test_esquema_v2_postgres.py."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from sqlalchemy.exc import IntegrityError

from models import ACTOR_REF_R13, RANGO_DE_MEDICION, Accion, Asignacion, Dispositivo, Medicion
from schemas import LIMITES_TELEMETRIA
from servicios import mediciones
from tests.base import PruebaApi, importar_listener, modulo_migracion_v2

BACKEND = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class RolPorAsignacion(PruebaApi):
    """Un mismo usuario es dueño del Domo A y administrador del Domo B."""

    def setUp(self):
        super().setUp()
        self.otro_dueno = self.usuario("otro@prueba.co")
        self.domo_b = self.domo("Domo B", self.otro_dueno)
        self.asignar(self.dueno, self.domo_b)
        self.db.commit()
        self.toma_a = self.toma("ta")
        self.toma_b = self.toma("tb", domo_id=self.domo_b.id)

    def test_el_token_y_el_perfil_dicen_dueno(self):
        self.assertEqual(self.client.get("/users/me", headers=self.auth()).json()["rol"], "dueño")
        self.assertEqual(self.client.get("/users/me", headers=self.auth(self.admin)).json()["rol"], "administrador")

    def test_ve_los_dos_domos(self):
        domos = self.client.get("/domos", headers=self.auth()).json()
        self.assertEqual([d["nombre"] for d in domos], ["Domo A", "Domo B"])
        self.assertEqual({d["nombre"]: d["propietario_id"] for d in domos}, {"Domo A": self.dueno.id, "Domo B": self.otro_dueno.id})

    def test_en_su_domo_es_dueno(self):
        self.assertEqual(self.patch_dispositivo(self.toma_a, {"nombre": "Nevera"}).status_code, 200)
        self.assertEqual(self.client.put(self.ruta("/configuracion"), json={"perfil_control": "equilibrado"}, headers=self.auth()).status_code, 200)
        self.assertEqual(self.crear({"tipo": "toma", "nombre": "n"}).status_code, 201)

    def test_en_el_otro_solo_opera(self):
        ruta_b = self.ruta(f"/dispositivos/{self.toma_b.id}", self.domo_b)
        self.assertEqual(self.client.patch(ruta_b, json={"estado": "apagado"}, headers=self.auth()).status_code, 200)
        self.assertEqual(self.client.patch(ruta_b, json={"nombre": "x"}, headers=self.auth()).status_code, 403)
        self.assertEqual(
            self.client.put(self.ruta("/configuracion", self.domo_b), json={"perfil_control": "maximo_uso"}, headers=self.auth()).status_code,
            403,
        )
        self.assertEqual(self.client.post(self.ruta("/dispositivos", self.domo_b), json={"tipo": "toma", "nombre": "n"}, headers=self.auth()).status_code, 403)
        self.assertEqual(self.client.put(self.ruta("", self.domo_b), json={"nombre": "x"}, headers=self.auth()).status_code, 403)
        self.assertEqual(self.client.get(self.ruta("/administradores", self.domo_b), headers=self.auth()).status_code, 403)

    def test_su_accion_queda_con_el_rol_de_cada_domo(self):
        self.patch_dispositivo(self.toma_a, {"estado": "apagado"})
        self.client.patch(self.ruta(f"/dispositivos/{self.toma_b.id}", self.domo_b), json={"estado": "apagado"}, headers=self.auth())
        self.db.expire_all()
        actores = {a.dispositivo_id: (a.actor_tipo, a.actor_ref) for a in self.db.query(Accion)}
        self.assertEqual(actores, {self.toma_a.id: ("dueno", self.dueno.correo), self.toma_b.id: ("administrador", self.dueno.correo)})

    def test_crear_un_domo_crea_su_asignacion_de_dueno(self):
        creado = self.client.post("/domos", json={"nombre": "Nuevo"}, headers=self.auth())
        self.assertEqual(creado.status_code, 201, creado.text)
        self.assertEqual(creado.json()["propietario_id"], self.dueno.id)
        self.db.expire_all()
        asignacion = self.db.query(Asignacion).filter(Asignacion.domo_id == creado.json()["id"]).one()
        self.assertEqual((asignacion.usuario_id, asignacion.rol), (self.dueno.id, "dueno"))
        # Un administrador (sin domos propios) no crea domos.
        self.assertEqual(self.client.post("/domos", json={"nombre": "x"}, headers=self.auth(self.admin)).status_code, 403)


class AccionesPorJoin(PruebaApi):
    def test_el_historial_de_un_domo_sale_de_sus_dispositivos(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        toma_a, toma_b = self.toma("ta"), self.toma("tb", domo_id=otro.id)
        self.patch_dispositivo(toma_a, {"estado": "apagado"})
        self.patch_dispositivo(toma_a, {"estado": "encendido"}, self.admin)
        self.client.patch(self.ruta(f"/dispositivos/{toma_b.id}", otro), json={"estado": "apagado"}, headers=self.auth())
        self.soc(10)
        self.evaluar()

        filas = self.client.get(self.ruta("/historial"), headers=self.auth()).json()
        self.assertEqual({f["dispositivo_id"] for f in filas}, {toma_a.id})
        self.assertEqual({f["domo_id"] for f in filas}, {self.domo_a.id})
        self.assertEqual([f["rol"] for f in filas], ["sistema", "administrador", "dueño"])
        sistema, admin, dueno = filas
        self.assertEqual((sistema["usuario_id"], sistema["nombre_usuario"], sistema["nombre_dispositivo"]), (None, None, "ta"))
        self.assertEqual((admin["usuario_id"], dueno["usuario_id"]), (self.admin.id, self.dueno.id))
        self.assertEqual((dueno["accion_realizada"], dueno["valor_anterior"], dueno["valor_nuevo"]), ("apagar", "encendido", "apagado"))
        self.assertEqual(dueno["condicion_control"], "control manual")

        del_b = self.client.get(self.ruta("/historial", otro), headers=self.auth()).json()
        self.assertEqual([f["dispositivo_id"] for f in del_b], [toma_b.id])

    def test_filtros_de_rol_con_los_nombres_de_v1(self):
        toma = self.toma("t")
        self.patch_dispositivo(toma, {"estado": "apagado"})
        self.patch_dispositivo(toma, {"estado": "encendido"}, self.admin)
        for rol, esperado in (("dueño", 1), ("administrador", 1), ("sistema", 0)):
            with self.subTest(rol=rol):
                filas = self.client.get(self.ruta("/historial"), params={"rol": rol}, headers=self.auth()).json()
                self.assertEqual(len(filas), esperado)

    def test_la_accion_de_r13_lleva_r13_como_actor(self):
        self.toma("t")
        self.soc(10)
        self.evaluar()
        accion = self.db.query(Accion).one()
        self.assertEqual((accion.actor_tipo, accion.actor_ref, accion.condicion), ("sistema", ACTOR_REF_R13, "modo=supervivencia; soc=10.0"))


class AlertasPorDispositivo(PruebaApi):
    def crear(self, **campos):
        cuerpo = {"tipo_alerta": "dispositivo_desconectado", "nivel_prioridad": "advertencia", "descripcion": "Sin conexión", **campos}
        return self.client.post(self.ruta("/alertas"), json=cuerpo, headers=self.auth())

    def test_filtrar_por_id_fisico_ref(self):
        uno = self.crear(id_fisico_ref="toma1").json()
        self.crear(id_fisico_ref="toma2")
        self.crear(tipo_alerta="sobrecarga")
        self.assertEqual((uno["id_fisico_ref"], uno["creada_por_r13"]), ("toma1", False))
        filas = self.client.get(self.ruta("/alertas"), params={"id_fisico_ref": "toma1"}, headers=self.auth()).json()
        self.assertEqual([f["id"] for f in filas], [uno["id"]])
        self.assertEqual(len(self.client.get(self.ruta("/alertas"), headers=self.auth()).json()), 3)

    def test_tipo_nivel_y_descripcion_son_obligatorios(self):
        for falta in ("tipo_alerta", "nivel_prioridad", "descripcion"):
            with self.subTest(falta=falta):
                cuerpo = {"tipo_alerta": "sobrecarga", "nivel_prioridad": "critico", "descripcion": "x"}
                del cuerpo[falta]
                self.assertEqual(self.client.post(self.ruta("/alertas"), json=cuerpo, headers=self.auth()).status_code, 422)
        self.assertEqual(self.crear(descripcion="  ").status_code, 422)

    def test_atendida_sin_fecha_la_rechaza_la_base(self):
        alerta = self.crear().json()
        fila = self.db.get(tests.base.Alerta, alerta["id"])
        fila.estado = "atendida"
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()


class DispositivosNoSeBorran(PruebaApi):
    def test_con_acciones_la_base_no_deja_borrarlo(self):
        toma = self.toma()
        self.patch_dispositivo(toma, {"estado": "apagado"})
        self.db.expire_all()
        self.db.delete(self.db.get(Dispositivo, toma.id))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.assertIsNotNone(self.db.get(Dispositivo, toma.id))

    def test_la_api_no_tiene_delete(self):
        toma = self.toma()
        self.assertEqual(self.client.delete(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).status_code, 405)

    def test_un_domo_con_dispositivos_tampoco(self):
        self.toma()
        self.db.delete(self.domo_a)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_las_reglas_del_dispositivo_estan_en_la_base(self):
        for campos in (
            {"tipo": "toma", "brillo": 50},
            {"tipo": "bombillo", "categoria": "confort"},
            {"activo": False},
            {"desactivado_en": T0},
            {"tipo": "otro"},
        ):
            with self.subTest(campos=campos):
                self.db.add(Dispositivo(**{"domo_id": self.domo_a.id, "nombre": "x", "tipo": "toma", "categoria": "esencial", **campos}))
                with self.assertRaises(IntegrityError):
                    self.db.commit()
                self.db.rollback()


class Mediciones(PruebaApi):
    def setUp(self):
        super().setUp()
        # Las lecturas de esta clase son de T0: el reloj de la vigencia también.
        reloj = patch("servicios.mediciones.ahora_utc", return_value=T0 + timedelta(seconds=30))
        reloj.start()
        self.addCleanup(reloj.stop)

    def guardar(self, tema, valores, segundos=0, domo=None):
        bloque = mediciones.guardar_medicion(self.db, (domo or self.domo_a).id, tema, valores, T0 + timedelta(seconds=segundos))
        self.db.commit()
        return bloque

    def filas(self):
        self.db.expire_all()
        return self.db.query(Medicion).order_by(Medicion.domo_id, Medicion.fecha_hora).all()

    def test_el_bloque_es_de_10_segundos_en_utc(self):
        self.assertEqual(mediciones.inicio_de_bloque(T0 + timedelta(seconds=19.9)), T0 + timedelta(seconds=10))
        bogota = datetime(2026, 11, 14, 7, 0, 5, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(mediciones.inicio_de_bloque(bogota), T0)
        self.assertEqual(self.guardar("bateria", {"estado_carga_soc": 50}, 7), T0)

    def test_instantaneos_se_reemplazan_y_la_energia_se_suma(self):
        self.guardar("consumo", {"potencia_consumida": 60, "energia_consumida": 0.1, "voltaje": 120})
        self.guardar("consumo", {"potencia_consumida": 80, "energia_consumida": 0.2}, 4)
        (fila,) = self.filas()
        self.assertEqual(fila.potencia_consumo_w, 80)
        # La lectura nueva reemplaza todo su tema, también lo que llega en null.
        self.assertIsNone(fila.voltaje_ac_v)
        self.assertAlmostEqual(fila.energia_consumo_wh, 0.3)

    def test_energia_nula_solo_si_las_dos_lo_son(self):
        self.guardar("fotovoltaica", {"potencia_generada": 10})
        self.guardar("fotovoltaica", {"potencia_generada": 12}, 1)
        self.assertIsNone(self.filas()[0].energia_fv_wh)
        self.guardar("fotovoltaica", {"energia_generada": 0.5}, 2)
        self.guardar("fotovoltaica", {"potencia_generada": 12}, 3)
        self.assertEqual(self.filas()[0].energia_fv_wh, 0.5)

    def test_los_otros_temas_no_se_tocan(self):
        self.guardar("bateria", {"estado_carga_soc": 70, "temperatura": 25})
        self.guardar("fotovoltaica", {"potencia_generada": 100, "voltaje": 48}, 2)
        self.guardar("sensores", {"variable_medida": "frecuencia", "valor": 60}, 3)
        self.guardar("sensores", {"variable_medida": "factor_potencia", "valor": 0.9}, 4)
        (fila,) = self.filas()
        self.assertEqual(
            (fila.soc_pct, fila.temperatura_bateria_c, fila.potencia_fv_w, fila.voltaje_fv_v, fila.frecuencia_hz, fila.factor_potencia),
            (70, 25, 100, 48, 60, 0.9),
        )

    def test_bloques_y_domos_distintos_son_filas_distintas(self):
        otro = self.domo("Domo B", self.dueno)
        self.db.commit()
        self.guardar("bateria", {"estado_carga_soc": 70})
        self.guardar("bateria", {"estado_carga_soc": 71}, 10)
        self.guardar("bateria", {"estado_carga_soc": 72}, 0, otro)
        self.assertEqual([(f.domo_id, f.soc_pct) for f in self.filas()], [(self.domo_a.id, 70), (self.domo_a.id, 71), (otro.id, 72)])

    def test_variable_de_sensor_sin_columna(self):
        with self.assertRaises(mediciones.MedicionInvalida):
            mediciones.guardar_medicion(self.db, self.domo_a.id, "sensores", {"variable_medida": "temperatura", "valor": 1})
        respuesta = self.client.post(self.ruta("/lecturas-sensores"), json={"variable_medida": "temperatura", "valor": 1}, headers=self.auth())
        self.assertEqual(respuesta.status_code, 422)

    def test_consumo_por_dispositivo_ya_no_se_admite(self):
        toma = self.toma()
        respuesta = self.client.post(self.ruta("/lecturas-consumo"), json={"dispositivo_id": toma.id, "potencia_consumida": 5}, headers=self.auth())
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.filas(), [])

    def test_las_lecturas_de_la_api_salen_de_mediciones(self):
        self.guardar("bateria", {"estado_carga_soc": 70, "potencia": -12.5, "estado_bateria": "descargando"})
        self.guardar("fotovoltaica", {"potencia_generada": 4.9}, 1)
        self.guardar("fotovoltaica", {"potencia_generada": 5.1}, 11)
        self.guardar("sensores", {"variable_medida": "frecuencia", "valor": 60}, 12)
        cabeceras = self.auth()
        bateria = self.client.get(self.ruta("/lecturas-bateria"), headers=cabeceras).json()
        self.assertEqual([(b["estado_carga_soc"], b["potencia"], b["estado_bateria"]) for b in bateria], [(70, -12.5, "descargando")])
        fv = self.client.get(self.ruta("/lecturas-fotovoltaicas"), headers=cabeceras).json()
        # estado_generacion se calcula con el umbral del firmware (5 W).
        self.assertEqual([(f["potencia_generada"], f["estado_generacion"]) for f in fv], [(5.1, "generando"), (4.9, "sin_generacion")])
        self.assertEqual(self.client.get(self.ruta("/lecturas-consumo"), headers=cabeceras).json(), [])
        self.assertEqual(self.client.get(self.ruta("/lecturas-consumo/ultima"), headers=cabeceras).status_code, 404)
        sensores = self.client.get(self.ruta("/lecturas-sensores"), headers=cabeceras).json()
        self.assertEqual([(s["variable_medida"], s["valor"], s["unidad_medida"]) for s in sensores], [("frecuencia", 60, "Hz")])
        general = self.client.get(self.ruta("/estado-general"), headers=cabeceras).json()
        self.assertEqual((general["estado_carga_soc"], general["potencia_generada"], general["potencia_consumida"]), (70, 5.1, None))

    def test_historico_suma_la_energia_de_mediciones(self):
        self.guardar("fotovoltaica", {"potencia_generada": 100, "energia_generada": 0.3})
        self.guardar("fotovoltaica", {"potencia_generada": 200, "energia_generada": 0.5}, 10)
        self.guardar("consumo", {"potencia_consumida": 50, "energia_consumida": 0.1}, 10)
        datos = self.client.get(
            self.ruta("/historico"),
            params={"fecha_desde": (T0 - timedelta(minutes=1)).isoformat(), "fecha_hasta": (T0 + timedelta(minutes=1)).isoformat()},
            headers=self.auth(),
        ).json()
        self.assertAlmostEqual(datos["energia_generada_total"], 0.8)
        self.assertEqual((datos["potencia_generada_maxima"], datos["potencia_generada_promedio"]), (200, 150))
        self.assertAlmostEqual(datos["energia_consumida_total"], 0.1)
        self.assertEqual(datos["potencia_consumida_promedio"], 50)

    def test_r13_usa_la_ultima_medicion_con_datos_de_bateria(self):
        self.toma("t")
        self.guardar("bateria", {"estado_carga_soc": 30})
        # Una fila posterior sin batería no cuenta como "la última lectura de batería".
        self.guardar("fotovoltaica", {"potencia_generada": 100}, 20)
        self.assertEqual(self.evaluar(), [("t", "apagar")])

    def test_la_ultima_lectura_de_bateria_sin_soc_no_se_evalua(self):
        self.toma("t")
        self.guardar("bateria", {"estado_carga_soc": 30})
        self.guardar("bateria", {"voltaje": 12.1}, 20)
        respuesta = self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 409)

    def test_los_check_de_la_base_son_los_limites_de_telemetria(self):
        base = modulo_migracion_v2("2b8e4f1a6c90_esquema_base_v2").RANGOS_MEDICIONES
        nuevos = modulo_migracion_v2("7c3d9a1e4b52_mediciones_voltaje_fv_y_temperatura").RANGOS_NUEVOS
        self.assertEqual({columna: LIMITES_TELEMETRIA[campo] for columna, campo in RANGO_DE_MEDICION.items()}, {**base, **nuevos})
        columnas = {c.name for c in Medicion.__table__.columns} - {"id", "domo_id", "fecha_hora", "estado_bateria"}
        self.assertEqual(columnas, set(RANGO_DE_MEDICION))
        self.assertEqual(set(mediciones.COLUMNAS_POR_TEMA["bateria"]) - {"estado_bateria"} | {
            c for tema in ("fotovoltaica", "consumo", "sensores") for c in mediciones.COLUMNAS_POR_TEMA[tema]
        }, columnas)

    def test_fuera_de_rango_lo_rechaza_la_base(self):
        for columna in ("soc_pct", "potencia_fv_w", "temperatura_bateria_c"):
            with self.subTest(columna=columna):
                self.db.add(Medicion(domo_id=self.domo_a.id, fecha_hora=T0, **{columna: 1e9 + 1}))
                with self.assertRaises(IntegrityError):
                    self.db.commit()
                self.db.rollback()


class ListenerV2(PruebaApi):
    def setUp(self):
        super().setUp()
        self.listener = importar_listener()
        from sqlalchemy.orm import sessionmaker

        parche = patch.object(self.listener, "SesionListener", sessionmaker(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False))
        parche.start()
        self.addCleanup(parche.stop)
        self.listener._domos_conocidos.clear()

    def procesar(self, tipo, payload):
        import json

        self.listener._procesar_mensaje(f"domo/{self.domo_a.id}/telemetria/{tipo}", json.dumps(payload).encode())
        self.db.expire_all()
        return self.db.query(Medicion).all()

    def test_mismo_contrato_mqtt_y_energia_sumada(self):
        momento = T0.isoformat()
        self.procesar("consumo", {"voltaje": 120, "corriente": 0.5, "potencia_consumida": 60, "energia_consumida": 0.17, "fecha_hora": momento})
        self.procesar("fotovoltaica", {"voltaje": None, "corriente": 2, "potencia_generada": 120, "energia_generada": 0.3, "estado_generacion": "generando", "fecha_hora": momento})
        (fila,) = self.procesar("consumo", {"potencia_consumida": 70, "energia_consumida": 0.2, "fecha_hora": momento})
        self.assertEqual((fila.potencia_consumo_w, fila.potencia_fv_w, fila.corriente_fv_a), (70, 120, 2))
        self.assertAlmostEqual(fila.energia_consumo_wh, 0.37)

    def test_consumo_con_dispositivo_se_descarta(self):
        with self.assertLogs("mqtt_listener", "ERROR"):
            self.assertEqual(self.procesar("consumo", {"dispositivo_id": 1, "potencia_consumida": 5}), [])

    def test_sensor_sin_columna_se_descarta(self):
        with self.assertLogs("mqtt_listener", "ERROR"):
            self.assertEqual(self.procesar("sensores", {"tipo_sensor": "x", "variable_medida": "humedad", "valor": 50}), [])

    def test_el_pzem_del_firmware_se_guarda(self):
        (fila,) = self.procesar("sensores", {"tipo_sensor": "pzem004t", "variable_medida": "frecuencia", "valor": 60.1, "unidad_medida": "Hz", "estado": "ok"})
        self.assertEqual(fila.frecuencia_hz, 60.1)

    def test_bateria_dispara_r13(self):
        self.toma("t")
        with patch.object(self.listener, "R13_AUTO", True):
            self.listener._ultima_evaluacion_r13.clear()
            self.procesar("bateria", {"estado_carga_soc": 20})
        self.assertEqual(self.recargar(self.db.query(Dispositivo).one()).estado, "apagado")


class EstadoComunicacion(PruebaApi):
    def ruta_de(self, toma, sufijo=""):
        return self.ruta(f"/dispositivos/{toma.id}/estado-comunicacion{sufijo}")

    def test_guarda_el_ultimo_estado_en_el_dispositivo(self):
        toma = self.toma()
        self.assertEqual(self.client.get(self.ruta_de(toma, "/ultima"), headers=self.auth()).status_code, 404)
        self.assertEqual(self.client.get(self.ruta_de(toma), headers=self.auth()).json(), [])
        creado = self.client.post(self.ruta_de(toma), json={"estado_conexion": "conectado", "senal": "-60"}, headers=self.auth())
        self.assertEqual(creado.status_code, 201, creado.text)
        self.assertEqual((creado.json()["estado_conexion"], creado.json()["senal"]), ("conectado", None))
        general = self.client.get(self.ruta("/estado-general"), headers=self.auth()).json()
        self.assertEqual(general["dispositivos_conectados"], 1)
        self.client.post(self.ruta_de(toma), json={"estado_conexion": "desconectado"}, headers=self.auth())
        self.assertEqual(self.client.get(self.ruta_de(toma, "/ultima"), headers=self.auth()).json()["estado_conexion"], "desconectado")
        self.assertEqual(len(self.client.get(self.ruta_de(toma), headers=self.auth()).json()), 1)
        dispositivo = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertEqual(dispositivo["estado_conexion"], "desconectado")


class Conexion(TestCase):
    """database.py usa DATABASE_URL_V2 y nunca DATABASE_URL."""

    def correr(self, entorno):
        codigo = "import database; print(database.engine.url.get_backend_name())"
        limpio = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DATABASE_URL_V2")}
        return subprocess.run(
            [sys.executable, "-c", f"import dotenv; dotenv.load_dotenv = lambda *a, **k: None; {codigo}"],
            cwd=BACKEND, env={**limpio, "PYTHONUTF8": "1", **entorno}, capture_output=True, text=True, encoding="utf-8",
        )

    def test_sin_v2_falla_con_un_mensaje_claro_aunque_haya_v1(self):
        resultado = self.correr({"DATABASE_URL": "sqlite:///v1-secreta.db"})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("DATABASE_URL_V2 no está definida", resultado.stderr)
        self.assertNotIn("v1-secreta", resultado.stderr)

    def test_con_v2_la_usa(self):
        resultado = self.correr({"DATABASE_URL_V2": "sqlite://", "DATABASE_URL": "postgresql://u@localhost/v1"})
        self.assertEqual((resultado.returncode, resultado.stdout.strip()), (0, "sqlite"), resultado.stderr)


class RespaldoConOrigen(TestCase):
    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("respaldo_bd", BACKEND / "tools" / "respaldo_bd.py")
        self.modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.modulo)

    def test_cada_origen_lee_su_variable(self):
        entorno = {"DATABASE_URL": "sqlite:///v1.db", "DATABASE_URL_V2": "sqlite:///v2.db"}
        with patch.dict(os.environ, entorno), patch("dotenv.load_dotenv"):
            for origen, archivo in (("v1", "v1.db"), ("v2", "v2.db")):
                with self.subTest(origen=origen):
                    motor = self.modulo.motor_de(origen)
                    self.assertEqual(motor.url.database, archivo)
                    motor.dispose()

    def test_sin_la_variable_termina_sin_mostrar_nada(self):
        with patch.dict(os.environ, {"DATABASE_URL_V2": ""}), patch("dotenv.load_dotenv"), self.assertRaises(SystemExit) as salida:
            self.modulo.motor_de("v2")
        self.assertIn("DATABASE_URL_V2 no está definida", str(salida.exception.code))

    def test_por_defecto_es_v1_y_el_manifiesto_lo_dice(self):
        fuente = (BACKEND / "tools" / "respaldo_bd.py").read_text(encoding="utf-8")
        self.assertIn('default="v1"', fuente)
        self.assertIn('"origen": args.origen', fuente)
