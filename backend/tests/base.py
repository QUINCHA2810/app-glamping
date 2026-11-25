"""
Base de las pruebas de la API (modelo v2). Corren sobre SQLite en memoria, sin red.

DATABASE_URL_V2 (y DATABASE_URL) se fijan ANTES de importar la aplicación:
load_dotenv() no pisa una variable ya definida, así que estas pruebas nunca pueden
llegar a Supabase.

Los fixtures crean las filas como lo hace la API: un domo nace con la asignación
'dueno' de su dueño, y un administrador ve un domo solo con una asignación. Los
umbrales de las tomas no se guardan: salen de la categoría y del perfil y la batería
del domo (por defecto lifepo4 y conservador: prescindible 50/60, confort 35/45,
importante 20/30).
"""

import importlib.util
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

os.environ["DATABASE_URL_V2"] = "sqlite://"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "solo-para-pruebas-clave-larga-de-32-bytes-o-mas"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import database  # noqa: E402
import limite_login  # noqa: E402
from database import Base, get_db  # noqa: E402
from main import app  # noqa: E402
from models import ROL_ADMINISTRADOR, ROL_DUENO, Alerta, Asignacion, Dispositivo, Domo, Medicion, Usuario  # noqa: E402
from security import crear_token_acceso  # noqa: E402
from servicios.mediciones import guardar_medicion  # noqa: E402

VERSIONES = Path(__file__).resolve().parent.parent / "alembic" / "versions"
VERSIONES_V2 = Path(__file__).resolve().parent.parent / "alembic_v2" / "versions"


def _modulo_migracion(nombre: str, carpeta: Path = VERSIONES):
    spec = importlib.util.spec_from_file_location(nombre, carpeta / f"{nombre}.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def modulo_migracion_v2(nombre: str):
    return _modulo_migracion(nombre, VERSIONES_V2)


def importar_listener():
    """mqtt_listener arma su motor al importarse, con opciones de pool que SQLite
    en memoria no acepta. Se le da una URL de archivo a la que nunca se conecta:
    cada prueba reemplaza SesionListener."""
    with patch.object(database, "DATABASE_URL", "sqlite:///sin_uso.db"):
        return importlib.import_module("mqtt_listener")


class PruebaApi(TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(engine, "connect", lambda conexion, _: conexion.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        Sesion = sessionmaker(bind=engine, autoflush=False)

        self.db = Sesion()
        self.addCleanup(self.db.close)

        def _get_db():
            db = Sesion()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _get_db
        self.addCleanup(app.dependency_overrides.clear)
        # raise_server_exceptions=False: un error interno se ve como un 500 de verdad.
        self.client = TestClient(app, raise_server_exceptions=False)
        # Los fallos de /auth/login se cuentan en memoria: cada prueba empieza de cero.
        limite_login.reiniciar()
        self.addCleanup(limite_login.reiniciar)

        for ruta in (
            "routers.dispositivos.publicar_comando_toma",
            "routers.dispositivos.publicar_comando_bombillo",
            "servicios.control_energetico.publicar_comando_toma",
        ):
            parche = patch(ruta)
            parche.start()
            self.addCleanup(parche.stop)

        self.dueno = self.usuario("dueno@prueba.co", "dueño")
        self.admin = self.usuario("admin@prueba.co", "administrador")
        self.domo_a = self.domo("Domo A", self.dueno)
        self.asignar(self.admin, self.domo_a)
        self.db.commit()

    # Datos -------------------------------------------------------------------

    def usuario(self, correo: str, rol: str = ROL_ADMINISTRADOR) -> Usuario:
        """La cuenta. En v2 el rol no es de la cuenta: 'dueño' de un domo lo da
        domo(), y 'administrador' de un domo, asignar(). `rol` solo documenta."""
        usuario = Usuario(nombre=correo, correo=correo, contrasena_hash="x")
        self.db.add(usuario)
        self.db.flush()
        return usuario

    def domo(self, nombre: str, dueno: Usuario, **campos) -> Domo:
        """El domo con su asignación 'dueno', como POST /domos."""
        domo = Domo(nombre=nombre, **campos)
        self.db.add(domo)
        self.db.flush()
        self.db.add(Asignacion(usuario_id=dueno.id, domo_id=domo.id, rol=ROL_DUENO))
        self.db.flush()
        return domo

    def asignar(self, admin: Usuario, domo: Domo) -> None:
        self.db.add(Asignacion(usuario_id=admin.id, domo_id=domo.id, rol=ROL_ADMINISTRADOR))
        self.db.flush()

    def toma(self, nombre="toma", categoria="confort", **campos) -> Dispositivo:
        """Fila directa en la base, encendida por defecto. Sus umbrales salen de la
        categoría y del domo (no se guardan)."""
        valores = {
            "domo_id": self.domo_a.id,
            "tipo": "toma",
            "nombre": nombre,
            "estado": "encendido",
            "categoria": categoria,
            **campos,
        }
        toma = Dispositivo(**valores)
        self.db.add(toma)
        self.db.commit()
        return toma

    def bombillo(self, nombre="bombillo") -> Dispositivo:
        bombillo = Dispositivo(domo_id=self.domo_a.id, tipo="bombillo", nombre=nombre, categoria="esencial")
        self.db.add(bombillo)
        self.db.commit()
        return bombillo

    def soc(self, valor: float, domo: Domo | None = None, fecha_hora=None) -> None:
        """Una lectura de batería, con la misma función que el listener. Varias
        seguidas caen en el mismo bloque de 10 s: la última reemplaza el SoC."""
        guardar_medicion(self.db, (domo or self.domo_a).id, "bateria", {"estado_carga_soc": valor}, fecha_hora)
        self.db.commit()

    def perfil(self, perfil: str, tipo_bateria: str | None = None, domo: Domo | None = None) -> None:
        """Cambia el perfil (y la batería) del domo directo en la base."""
        domo = domo or self.domo_a
        domo.perfil = perfil
        if tipo_bateria is not None:
            domo.bateria_tipo = tipo_bateria
        self.db.commit()

    # API ---------------------------------------------------------------------

    def auth(self, usuario: Usuario | None = None) -> dict:
        return {"Authorization": f"Bearer {crear_token_acceso(usuario or self.dueno)}"}

    def ruta(self, sufijo: str = "", domo: Domo | None = None) -> str:
        return f"/domos/{(domo or self.domo_a).id}{sufijo}"

    def patch_dispositivo(self, dispositivo: Dispositivo, cuerpo: dict, usuario: Usuario | None = None):
        return self.client.patch(self.ruta(f"/dispositivos/{dispositivo.id}"), json=cuerpo, headers=self.auth(usuario))

    def crear(self, cuerpo: dict, usuario: Usuario | None = None):
        return self.client.post(self.ruta("/dispositivos"), json=cuerpo, headers=self.auth(usuario))

    def evaluar(self) -> list[tuple[str, str]]:
        respuesta = self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return [(a["nombre"], a["accion_realizada"]) for a in respuesta.json()["acciones"]]

    def modo(self) -> str:
        respuesta = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()["modo_actual"]

    def alertas_bateria(self) -> list[Alerta]:
        self.db.expire_all()
        return self.db.query(Alerta).filter(Alerta.tipo == "bateria_baja").all()

    def recargar(self, dispositivo: Dispositivo) -> Dispositivo:
        self.db.expire_all()
        return self.db.get(Dispositivo, dispositivo.id)

    def poner_bateria(self, tipo: str, domo: Domo | None = None) -> None:
        respuesta = self.client.put(
            self.ruta("/configuracion", domo),
            json={"bateria": {"tipo": tipo, "capacidad_wh": 1200}},
            headers=self.auth(),
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
