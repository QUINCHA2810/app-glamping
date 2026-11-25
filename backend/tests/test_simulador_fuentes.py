"""El simulador exige --domo y no se mezcla con otra fuente de telemetría."""

import contextlib
import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# tests.base primero: fija DATABASE_URL en SQLite antes de que algo importe database.py.
from tests.base import PruebaApi  # isort: skip

from sqlalchemy.orm import sessionmaker

from models import Medicion
from servicios.mediciones import guardar_medicion
from tests.test_telemetria_y_globales import _cargar_simulador

simulador = _cargar_simulador()
AHORA = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)
# Un dato de cada tema (v2: una lectura cuenta si trae algún valor de su tema).
DATO = {"bateria": {"estado_carga_soc": 70}, "fotovoltaica": {"potencia_generada": 100}, "consumo": {"potencia_consumida": 50}}


class Argumentos(PruebaApi):
    def test_domo_obligatorio(self):
        with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as salida:
            simulador.parsear_argumentos([])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn("--domo", error.getvalue())

    def test_con_domo_y_sin_forzar(self):
        args = simulador.parsear_argumentos(["--domo", "2"])
        self.assertEqual((args.domo, args.forzar, args.sin_bd), (2, False, False))
        self.assertTrue(simulador.parsear_argumentos(["--domo", "2", "--forzar"]).forzar)


class OtraFuente(PruebaApi):
    def setUp(self):
        super().setUp()
        self.domo_b = self.domo("Domo B", self.dueno)
        self.db.commit()
        self.Sesion = sessionmaker(bind=self.db.get_bind())

    def lectura(self, tema, minutos, domo=None, ahora=AHORA):
        guardar_medicion(self.db, (domo or self.domo_a).id, tema, DATO[tema], ahora - timedelta(minutes=minutos))
        self.db.commit()

    def motivo(self, domo=None):
        with self.Sesion() as db:
            return simulador.motivo_para_no_correr(db, (domo or self.domo_a).id, AHORA)

    def test_sin_telemetria_reciente_puede_correr(self):
        self.assertIsNone(self.motivo())
        self.lectura("bateria", 11)
        self.lectura("consumo", 60)
        self.assertIsNone(self.motivo())

    def test_cualquier_tipo_reciente_lo_impide(self):
        for tipo in ("bateria", "fotovoltaica", "consumo"):
            with self.subTest(tipo=tipo):
                self.db.query(Medicion).delete()
                self.db.commit()
                self.lectura(tipo, 3)
                motivo = self.motivo()
                self.assertIn(f"{tipo} hace 180 s", motivo)
                self.assertIn("últimos 10 min", motivo)
                # Solo ese tema: los otros no tienen datos en la fila.
                self.assertEqual(motivo.count(" hace "), 1)
                self.db.query(Medicion).delete()
                self.db.commit()

    def test_solo_cuenta_el_domo_elegido(self):
        self.lectura("bateria", 1, domo=self.domo_b)
        self.assertIsNone(self.motivo())
        self.assertIsNotNone(self.motivo(self.domo_b))

    def test_domo_inexistente(self):
        with self.Sesion() as db:
            self.assertIn("no existe", simulador.motivo_para_no_correr(db, 999, AHORA))

    def comprobar(self, *argv):
        args = simulador.parsear_argumentos(["--domo", str(self.domo_a.id), *argv])
        salida = io.StringIO()
        with patch("database.SessionLocal", self.Sesion), contextlib.redirect_stdout(salida):
            simulador.comprobar_que_no_hay_otra_fuente(args, AHORA)
        return salida.getvalue()

    def test_se_niega_y_forzar_solo_avisa(self):
        self.lectura("bateria", 2)
        with self.assertRaises(SystemExit) as salida:
            self.comprobar()
        self.assertIn("Si estás seguro, repite con --forzar", str(salida.exception.code))
        self.assertIn("ATENCIÓN (--forzar)", self.comprobar("--forzar"))

    def test_sin_bd_exige_forzar(self):
        with self.assertRaises(SystemExit) as salida:
            self.comprobar("--sin-bd")
        self.assertIn("--sin-bd", str(salida.exception.code))
        self.assertIn("ATENCIÓN", self.comprobar("--sin-bd", "--forzar"))

    def test_base_caida_se_niega_sin_mostrar_la_conexion(self):
        def caida():
            raise RuntimeError("postgresql://postgres:secreta@db.ejemplo.co/postgres")

        args = simulador.parsear_argumentos(["--domo", "1"])
        with patch("database.SessionLocal", caida), self.assertRaises(SystemExit) as salida:
            simulador.comprobar_que_no_hay_otra_fuente(args, AHORA)
        texto = str(salida.exception.code)
        self.assertIn("RuntimeError", texto)
        self.assertNotIn("secreta", texto)

    def test_sin_otra_fuente_no_dice_nada(self):
        self.assertEqual(self.comprobar(), "")

    def test_main_no_toca_mqtt_si_se_niega(self):
        # main() usa la hora real: la lectura tiene que ser de verdad reciente.
        self.lectura("fotovoltaica", 1, ahora=datetime.now(timezone.utc))

        def no_dormir(_):
            raise AssertionError("main() llegó al ciclo de publicación")

        with patch("sys.argv", ["simulador_telemetria.py", "--domo", str(self.domo_a.id)]), \
                patch("database.SessionLocal", self.Sesion), \
                patch.object(simulador.mqtt, "Client") as cliente, \
                patch.object(simulador.time, "sleep", no_dormir), \
                self.assertRaises(SystemExit):
            simulador.main()
        cliente.assert_not_called()
