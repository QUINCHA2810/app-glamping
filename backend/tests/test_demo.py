"""El generador del domo de demostración (tools/generar_datos_demo.py) y su marca.
Modelo v2: escribe domos (con su asignación), dispositivos, mediciones, acciones y alertas."""

import importlib.util
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from sqlalchemy import func
from models import Accion, Alerta, Dispositivo, Domo, Medicion
from servicios.mediciones import guardar_medicion
from servicios.categorias import RESERVA_SOC_POR_BATERIA, preset_de
from servicios.control_energetico import evaluar_control_energetico
from tests.base import PruebaApi
from tests.test_reportes import exportador, leer

RUTA = Path(__file__).resolve().parent.parent / "tools" / "generar_datos_demo.py"
FIN = datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)


def _cargar():
    spec = importlib.util.spec_from_file_location("generar_datos_demo", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


g = _cargar()
SIM = g.simular(g.Parametros(), FIN, con_traza=True)


def soc_de(accion):
    # "modo=supervivencia; soc=49.8" o, con primacía humana cortada por la reserva (DB-4),
    # "modo=supervivencia; soc=49.8; motivo=reserva".
    return float(accion["condicion"].split("soc=")[1].split(";")[0])


def motivo_de(accion):
    return "reserva" if "motivo=reserva" in accion["condicion"] else None


class Simulacion(TestCase):
    def test_forma(self):
        self.assertEqual(len(SIM.lecturas_bateria), 30 * 288)
        tiempos = [f["fecha_hora"] for f in SIM.lecturas_bateria]
        self.assertEqual(tiempos[-1], FIN)
        self.assertEqual(tiempos[0], FIN - timedelta(days=30) + timedelta(minutes=5))
        self.assertTrue(all(b - a == timedelta(minutes=5) for a, b in zip(tiempos, tiempos[1:])))
        self.assertEqual([f["fecha_hora"] for f in SIM.lecturas_fotovoltaicas], tiempos)
        self.assertTrue(all(0 <= f["estado_carga_soc"] <= 100 for f in SIM.lecturas_bateria))

    def test_curva_solar_de_bogota(self):
        for f in SIM.lecturas_fotovoltaicas:
            local = f["fecha_hora"].astimezone(g.ZONA_BOGOTA)
            hora = local.hour + local.minute / 60
            if hora < 5.75 or hora >= 18.0:
                self.assertEqual(f["potencia_generada"], 0, local)
        pico = max(f["potencia_generada"] for f in SIM.lecturas_fotovoltaicas)
        self.assertLessEqual(pico, 500 * 0.85)
        self.assertGreater(pico, 300)
        tipos = {d.tipo for d in SIM.dias}
        self.assertEqual(tipos, {g.DESPEJADO, g.PARCIAL, g.NUBLADO})

    def test_energia_coherente_con_la_potencia(self):
        for f in SIM.lecturas_consumo[:50]:
            self.assertAlmostEqual(f["energia_consumida"], f["potencia_consumida"] / 12, places=2)

    def test_reproducible_con_la_misma_semilla(self):
        otra = g.simular(g.Parametros(), FIN)
        for campo in ("lecturas_bateria", "lecturas_fotovoltaicas", "lecturas_consumo", "acciones", "alertas"):
            with self.subTest(campo=campo):
                self.assertEqual(getattr(otra, campo), getattr(SIM, campo))
        distinta = g.simular(g.Parametros(semilla=1), FIN)
        self.assertNotEqual(distinta.lecturas_fotovoltaicas, SIM.lecturas_fotovoltaicas)

    def test_el_clima_de_un_dia_no_depende_de_cuando_se_corra(self):
        despues = g.simular(g.Parametros(dias=5), FIN + timedelta(days=2))
        clima = {d.fecha: (d.tipo, d.nubes) for d in SIM.dias}
        comunes = [d for d in despues.dias if d.fecha in clima]
        self.assertGreaterEqual(len(comunes), 3)
        for d in comunes:
            self.assertEqual((d.tipo, d.nubes), clima[d.fecha])

    def test_hay_cortes_reactivaciones_y_acciones_manuales(self):
        sistema = [a for a in SIM.acciones if a["actor_tipo"] == "sistema"]
        manuales = [a for a in SIM.acciones if a["actor_tipo"] == "dueno"]
        self.assertTrue(any(a["accion"] == "apagar" for a in sistema))
        self.assertTrue(any(a["accion"] == "encender" for a in sistema))
        self.assertTrue(0 < len(manuales) < len(sistema))
        self.assertTrue(all(a["condicion"] == "control manual" for a in manuales))
        self.assertTrue(SIM.alertas)

    def test_r13_con_los_presets_del_backend(self):
        categoria = {clave: cat for clave, _, _, cat, _ in g.DISPOSITIVOS}
        self.assertEqual({t.clave: t.categoria for t in g.tomas_iniciales(g.Parametros())}, categoria)
        for a in SIM.acciones:
            if a["actor_tipo"] != "sistema":
                continue
            with self.subTest(accion=a):
                self.assertNotEqual(categoria[a["clave"]], "esencial")
                preset = preset_de(categoria[a["clave"]], "agm", "equilibrado")
                if a["accion"] == "apagar":
                    self.assertLess(soc_de(a), preset.umbral_apagado_soc)
                else:
                    self.assertGreaterEqual(soc_de(a), preset.umbral_reactivacion_soc)

    def test_la_reserva_nunca_se_viola_por_acciones_del_sistema(self):
        reserva = RESERVA_SOC_POR_BATERIA["agm"]
        encendidos = [a for a in SIM.acciones if a["actor_tipo"] == "sistema" and a["accion"] == "encender"]
        self.assertTrue(all(soc_de(a) >= reserva + 10 for a in encendidos))
        bajo_reserva = [paso for paso in SIM.traza if paso["soc"] < reserva]
        for paso in bajo_reserva:
            self.assertEqual(paso["encendidas"], set(), paso["t"])

    def test_una_toma_cortada_no_consume(self):
        sim = g.simular(g.Parametros(acciones_manuales=False), FIN, con_traza=True)
        for antes, paso in zip(sim.traza, sim.traza[1:]):
            for clave, w in paso["consumo_por_toma"].items():
                if clave != "bombillo" and clave not in antes["encendidas"]:
                    self.assertEqual(w, 0, (paso["t"], clave))

    def test_parametros_invalidos(self):
        for campos in ({"dias": 0}, {"dias": 400}, {"wp": 0}, {"capacidad_wh": -1}, {"tipo_bateria": "plomo"}, {"perfil": "x"}):
            with self.subTest(campos=campos), self.assertRaises(g.Detenido):
                g.simular(g.Parametros(**campos), FIN)


class MismasDecisionesQueElBackend(PruebaApi):
    """Se repiten las lecturas de SoC del generador, una a una, contra el R-13 real."""

    def test_reproduce_lo_que_hace_evaluar_control_energetico(self):
        parametros = g.Parametros(dias=4, wp=320, semilla=30)
        sim = g.simular(parametros, FIN)
        self.perfil(parametros.perfil, parametros.tipo_bateria)
        dispositivos = {}
        for toma in g.tomas_iniciales(parametros):
            d = Dispositivo(domo_id=self.domo_a.id, tipo=toma.tipo, nombre=toma.nombre, categoria=toma.categoria, estado=toma.estado)
            self.db.add(d)
            self.db.flush()
            dispositivos[toma.clave] = d
        self.db.commit()

        manuales = [a for a in sim.acciones if a["actor_tipo"] == "dueno"]
        reales = []
        ultima_id = 0
        for lectura in sim.lecturas_bateria:
            t = lectura["fecha_hora"]
            while manuales and manuales[0]["fecha_hora"] <= t:
                a = manuales.pop(0)
                d = dispositivos[a["clave"]]
                d.estado = a["estado_nuevo"]
                # Con la fecha simulada, igual que las de R-13 (abajo): la primacía humana
                # (DB-4) compara la fecha de la acción con la de las mediciones.
                self.db.add(
                    Accion(
                        dispositivo_id=d.id, accion=a["accion"], estado_anterior=a["estado_anterior"], estado_nuevo=a["estado_nuevo"],
                        actor_tipo="dueno", actor_ref=self.dueno.correo, resultado="ok", fecha_hora=a["fecha_hora"],
                    )
                )
            guardar_medicion(self.db, self.domo_a.id, "bateria", {"estado_carga_soc": lectura["estado_carga_soc"]}, t)
            self.db.commit()
            # El reloj de la lectura: con el de la pared, el SoC simulado sería viejo.
            resultado = evaluar_control_energetico(self.db, self.domo_a.id, ahora=t)
            reales.extend((x.nombre, x.accion_realizada, resultado.estado_carga_soc, x.motivo) for x in resultado.acciones)
            # Las acciones de R-13 llevan la hora de la pared: se pasan al reloj simulado,
            # un segundo después de la lectura, como en el generador.
            if resultado.acciones:
                self.db.query(Accion).filter(Accion.actor_tipo == "sistema", Accion.id > ultima_id).update(
                    {Accion.fecha_hora: t + timedelta(seconds=1)}
                )
                self.db.commit()
            ultima_id = self.db.query(func.max(Accion.id)).scalar() or 0

        nombre = {clave: d.nombre for clave, d in dispositivos.items()}
        generadas = [
            (nombre[a["clave"]], a["accion"], soc_de(a), motivo_de(a)) for a in sim.acciones if a["actor_tipo"] == "sistema"
        ]
        self.assertGreater(len(generadas), 4)
        self.assertEqual(reales, generadas)
        self.assertTrue(any(a["actor_tipo"] == "dueno" for a in sim.acciones))
        alertas = self.db.query(Alerta).filter(Alerta.domo_id == self.domo_a.id).order_by(Alerta.id).all()
        self.assertEqual(
            [(a.mensaje, a.nivel, a.valor_detectado, a.limite, a.estado, a.creada_por_r13) for a in alertas],
            [(a["mensaje"], a["nivel"], a["valor_detectado"], a["limite"], a["estado"], a["creada_por_r13"]) for a in sim.alertas],
        )
        self.assertTrue(alertas)


class Escritura(PruebaApi):
    PARAMETROS = g.Parametros(dias=3)

    def setUp(self):
        super().setUp()
        # Un domo real con datos: nada de esto puede cambiar.
        real = self.toma("Toma real", id_fisico="tuya-1")
        self.soc(64.0)
        self.db.add(
            Accion(
                dispositivo_id=real.id, accion="apagar", estado_anterior="encendido", estado_nuevo="apagado",
                actor_tipo="dueno", actor_ref=self.dueno.correo, resultado="ok",
            )
        )
        self.db.commit()
        self.dichos = []

    def foto_del_domo_real(self):
        self.db.expire_all()
        return g.filas_del_domo(self.db, self.domo_a.id), [(d.nombre, d.estado, d.id_fisico) for d in self.db.query(Dispositivo).filter(Dispositivo.domo_id == self.domo_a.id)]

    def correr(self, respuesta="s", **campos):
        argumentos = dict(dueno_correo="dueno@prueba.co", parametros=self.PARAMETROS, preguntar=lambda _: respuesta, decir=self.dichos.append, fin=FIN)
        argumentos.update(campos)
        return g.ejecutar(self.db, **argumentos)

    def demo(self):
        self.db.expire_all()
        return self.db.query(Domo).filter(Domo.nombre == g.NOMBRE_DEMO).one_or_none()

    def test_crea_el_domo_y_escribe_todo(self):
        antes = self.foto_del_domo_real()
        self.assertTrue(self.correr())
        domo = self.demo()
        self.assertTrue(domo.es_demostracion)
        self.assertEqual((domo.propietario_id, domo.ubicacion), (self.dueno.id, "Datos sintéticos, no es un domo real"))
        sim = g.simular(self.PARAMETROS, FIN)
        filas = g.filas_del_domo(self.db, domo.id)
        self.assertEqual(
            filas,
            {"mediciones": 3 * 288, "acciones": len(sim.acciones), "alertas": len(sim.alertas), "dispositivos": 4},
        )
        # Una fila por paso, con batería, generación y consumo juntos.
        fila = self.db.query(Medicion).filter(Medicion.domo_id == domo.id).order_by(Medicion.fecha_hora).first()
        self.assertTrue(all(v is not None for v in (fila.soc_pct, fila.potencia_fv_w, fila.potencia_consumo_w, fila.temperatura_bateria_c)))
        dispositivos = self.db.query(Dispositivo).filter(Dispositivo.domo_id == domo.id).order_by(Dispositivo.id).all()
        self.assertEqual([(d.nombre, d.categoria, d.id_fisico) for d in dispositivos], [
            ("Bombillo", "esencial", None), ("Nevera", "importante", None), ("Cargadores", "confort", None), ("Luces decorativas", "prescindible", None),
        ])
        self.assertEqual((domo.bateria_tipo, domo.bateria_capacidad_wh, domo.perfil), ("agm", 1200.0, "equilibrado"))
        manuales = (
            self.db.query(Accion)
            .join(Dispositivo, Dispositivo.id == Accion.dispositivo_id)
            .filter(Dispositivo.domo_id == domo.id, Accion.actor_tipo == "dueno")
            .all()
        )
        self.assertTrue(all(a.actor_ref == self.dueno.correo for a in manuales))
        self.assertEqual(self.foto_del_domo_real(), antes)
        texto = "\n".join(self.dichos)
        self.assertIn("mediciones              864", texto)
        self.assertIn("(se crea)", texto)

    def test_solo_calcular_y_cancelar_no_escriben(self):
        self.assertFalse(self.correr(solo_calcular=True))
        self.assertIn("Solo cálculo: no se escribió nada.", self.dichos)
        self.assertIsNone(self.demo())
        self.assertFalse(self.correr(respuesta="n"))
        self.assertIsNone(self.demo())

    def test_sin_reiniciar_no_toca_un_domo_con_datos(self):
        self.correr()
        domo = self.demo()
        antes = g.filas_del_domo(self.db, domo.id)
        with self.assertRaises(g.Detenido):
            self.correr()
        self.assertEqual(g.filas_del_domo(self.db, domo.id), antes)

    def test_reiniciar_borra_solo_el_domo_de_demostracion(self):
        self.correr()
        primero = self.demo()
        ids_viejos = {d.id for d in self.db.query(Dispositivo).filter(Dispositivo.domo_id == primero.id)}
        antes = self.foto_del_domo_real()
        self.assertTrue(self.correr(reiniciar=True, parametros=g.Parametros(dias=2)))
        domo = self.demo()
        self.assertEqual(domo.id, primero.id)
        self.assertEqual(g.filas_del_domo(self.db, domo.id)["mediciones"], 2 * 288)
        self.assertEqual(self.db.query(Dispositivo).filter(Dispositivo.domo_id == domo.id).count(), 4)
        self.assertTrue(ids_viejos)
        self.assertEqual(self.foto_del_domo_real(), antes)

    def test_nunca_escribe_en_un_domo_que_no_es_de_demostracion(self):
        antes = self.foto_del_domo_real()
        casos = [dict(domo_id=self.domo_a.id), dict(domo_id=self.domo_a.id, reiniciar=True), dict(domo_id=9999)]
        for campos in casos:
            with self.subTest(campos=campos), self.assertRaises(g.Detenido):
                self.correr(**campos)
        # Un domo real que se llama igual.
        self.domo(g.NOMBRE_DEMO, self.dueno)
        self.db.commit()
        with self.assertRaises(g.Detenido):
            self.correr(reiniciar=True)
        self.assertEqual(self.foto_del_domo_real(), antes)
        self.assertEqual(self.db.query(Medicion).count(), 1)

    def test_domo_de_otro_dueno_o_cuenta_que_no_es_duena(self):
        self.correr()
        domo = self.demo()
        otro = self.usuario("otro@prueba.co", "dueño")
        self.db.commit()
        with self.assertRaises(g.Detenido):
            self.correr(dueno_correo="otro@prueba.co", domo_id=domo.id, reiniciar=True)
        for correo in ("admin@prueba.co", "nadie@prueba.co"):
            with self.subTest(correo=correo), self.assertRaises(g.Detenido):
                self.correr(dueno_correo=correo)
        self.assertIsNotNone(otro)

    def test_si_algo_falla_no_queda_nada(self):
        import sqlalchemy

        original = sqlalchemy.insert

        def insertar(modelo):
            if modelo is Alerta:
                raise RuntimeError("falla a propósito")
            return original(modelo)

        antes = self.foto_del_domo_real()
        with patch("sqlalchemy.insert", insertar), self.assertRaises(RuntimeError):
            self.correr()
        self.assertIsNone(self.demo())
        self.assertEqual(self.db.query(Medicion).count(), 1)
        self.assertEqual(self.foto_del_domo_real(), antes)

    def test_la_app_y_las_exportaciones_lo_marcan(self):
        self.correr()
        domo = self.demo()
        domos = self.client.get("/domos", headers=self.auth()).json()
        self.assertEqual({d["nombre"]: d["es_demostracion"] for d in domos}, {"Domo A": False, g.NOMBRE_DEMO: True})
        hoy = FIN.astimezone(g.ZONA_BOGOTA).date()
        resumen = self.client.get(f"/domos/{domo.id}/reportes/resumen", params={"desde": hoy - timedelta(days=2), "hasta": hoy}, headers=self.auth())
        self.assertEqual(resumen.status_code, 200, resumen.text)
        datos = resumen.json()
        self.assertTrue(datos["es_demostracion"])
        self.assertGreater(datos["kwh_generados"], 0)
        self.assertFalse(self.client.get(self.ruta("/reportes/resumen"), params={"desde": hoy, "hasta": hoy}, headers=self.auth()).json()["es_demostracion"])
        with tempfile.TemporaryDirectory() as temporal:
            exportador.exportar(self.db, Path(temporal))
            filas = {f["nombre"]: (f["es_demostracion"], f["origen_datos"]) for f in leer(Path(temporal), "dim_domo.csv")}
        self.assertEqual(filas, {"Domo A": ("0", "Domo del proyecto"), g.NOMBRE_DEMO: ("1", "Demostración · datos sintéticos")})

    def test_la_api_no_cambia_la_marca(self):
        # Los campos que no conoce los ignora: la marca no cambia.
        self.assertFalse(self.client.put(self.ruta(), json={"es_demostracion": True}, headers=self.auth()).json()["es_demostracion"])
        creado = self.client.post("/domos", json={"nombre": "x", "es_demostracion": True}, headers=self.auth())
        self.assertEqual(creado.status_code, 201, creado.text)
        self.assertFalse(creado.json()["es_demostracion"])
        self.db.expire_all()
        self.assertEqual(self.db.query(Domo).filter(Domo.es_demostracion.is_(True)).count(), 0)


class LineaDeComandos(TestCase):
    def test_un_error_no_muestra_la_conexion(self):
        import sys

        class Falla:
            def __call__(self):
                raise RuntimeError("postgresql://postgres:secreta@db.ejemplo.co:5432/postgres")

        with patch.object(sys, "argv", ["generar_datos_demo.py", "--dueno-correo", "x@x.co", "--solo-calcular"]), patch("database.SessionLocal", Falla()):
            with self.assertRaises(SystemExit) as salida:
                g.main()
        self.assertNotIn("secreta", str(salida.exception.code))
        self.assertIn("RuntimeError", str(salida.exception.code))

    def test_el_codigo_no_imprime_la_url(self):
        fuente = RUTA.read_text(encoding="utf-8")
        self.assertNotIn("DATABASE_URL", fuente.split('"""', 2)[2])
        self.assertNotIn("os.environ", fuente)
