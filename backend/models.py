"""
Modelos SQLAlchemy del modelo de datos v2 (docs/modelo_v2/MAPEO.md): 8 tablas en
árbol, sin tablas de catálogo. El esquema real lo crean las migraciones de
alembic_v2/ (escritas a mano: no usar --autogenerate); estas clases lo reflejan.

    usuarios ──< asignaciones >── domos ──< dispositivos ──< acciones
                                    ├──< alertas
                                    ├──< mediciones
                                    └──< clima      (estimación de Open-Meteo, no medición)

Contrato de la API: los nombres de v1 que el código y las respuestas siguen usando
(`Alerta.tipo_alerta`, `Usuario.fecha_creacion`, `Domo.fecha_registro`…) son
`synonym` de la columna nueva, así que también sirven en filtros y ORDER BY. Lo que
en v2 ya no se guarda pero la API sigue devolviendo (`Usuario.rol`,
`Usuario.estado_cuenta`, `Domo.propietario_id`, `Dispositivo.estado_conexion`…) es
una propiedad calculada. Los umbrales y la prioridad de cada toma NO están aquí: se
calculan en servicios/categorias.py con la categoría, la batería y el perfil del domo.

Los CHECK y los ON DELETE se declaran también aquí para que las pruebas (SQLite)
apliquen las mismas reglas que la base. `BIGINT_PK`: en SQLite el autoincremento solo
funciona con una clave primaria INTEGER; en Postgres es BIGINT con IDENTITY.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    exists,
    false,
    func,
    select,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship, synonym

from database import Base
from schemas import LIMITES_TELEMETRIA
from servicios.iluminacion import MODOS_LUZ

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")

# Rol de una asignación en la base, y cómo lo llama la API (con ñ, como en v1: el
# frontend compara con 'dueño').
ROL_DUENO = "dueno"
ROL_ADMINISTRADOR = "administrador"
ROL_API = {ROL_DUENO: "dueño", ROL_ADMINISTRADOR: "administrador"}

# columna de mediciones -> campo de schemas.LIMITES_TELEMETRIA que define su rango.
# Los mismos rangos que los CHECK de alembic_v2 (tests/test_modelos_v2.py lo comprueba).
RANGO_DE_MEDICION = {
    "soc_pct": "estado_carga_soc",
    "voltaje_bateria_v": "voltaje",
    "corriente_bateria_a": "corriente",
    "potencia_bateria_w": "potencia",
    "temperatura_bateria_c": "temperatura",
    "voltaje_fv_v": "voltaje",
    "corriente_fv_a": "corriente",
    "potencia_fv_w": "potencia_generada",
    "energia_fv_wh": "energia_generada",
    "voltaje_ac_v": "voltaje",
    "corriente_ac_a": "corriente",
    "potencia_consumo_w": "potencia_consumida",
    "energia_consumo_wh": "energia_consumida",
    "frecuencia_hz": "valor",
    "factor_potencia": "valor",
}


def _en(columna: str, valores) -> str:
    return f"{columna} IN ({', '.join(repr(v) for v in valores)})"


# Bloque 1 — Identidad y acceso ---------------------------------------------

class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (CheckConstraint("version_sesion >= 0", name="usuarios_version_sesion_check"),)

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    apellido: Mapped[Optional[str]] = mapped_column(Text)
    correo: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    contrasena_hash: Mapped[str] = mapped_column(Text, nullable=False)
    telefono: Mapped[Optional[str]] = mapped_column(Text)
    # False = desactivada: no puede iniciar sesión y sus tokens dejan de servir.
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    # Viaja en el JWT. Subirla invalida todas las sesiones abiertas de la cuenta.
    version_sesion: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Tiene una contraseña temporal: hasta cambiarla, solo puede ver su perfil y cambiarla.
    debe_cambiar_contrasena: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    fecha_creacion = synonym("creado_en")
    asignaciones: Mapped[list["Asignacion"]] = relationship(back_populates="usuario")

    @property
    def estado_cuenta(self) -> str:
        return "activo" if self.activo else "inactivo"

    @estado_cuenta.setter
    def estado_cuenta(self, valor: str) -> None:
        self.activo = valor == "activo"

    @property
    def fotografia(self) -> None:
        """v2 no guarda fotografía (nadie la usaba). Se mantiene en UsuarioOut."""
        return None

    @property
    def rol(self) -> str:
        """Rol de la cuenta para el menú del frontend: 'dueño' si es dueña de algún
        domo, si no 'administrador'. NO decide permisos: cada endpoint de un domo mira
        la asignación en ESE domo (routers/domos.py, rol_en_domo)."""
        sesion = object_session(self)
        if sesion is None or self.id is None:
            return ROL_API[ROL_ADMINISTRADOR]
        es_dueno = sesion.execute(
            select(exists().where(Asignacion.usuario_id == self.id, Asignacion.rol == ROL_DUENO))
        ).scalar()
        return ROL_API[ROL_DUENO] if es_dueno else ROL_API[ROL_ADMINISTRADOR]


class Domo(Base):
    __tablename__ = "domos"
    __table_args__ = (
        CheckConstraint(_en("bateria_tipo", ("agm", "lifepo4")), name="domos_bateria_tipo_check"),
        CheckConstraint(
            "bateria_capacidad_wh > 0 AND bateria_capacidad_wh <= 10000000", name="domos_bateria_capacidad_check"
        ),
        CheckConstraint(_en("perfil", ("conservador", "equilibrado", "maximo_uso")), name="domos_perfil_check"),
        CheckConstraint(
            "(latitud IS NULL AND longitud IS NULL) OR "
            "(latitud IS NOT NULL AND longitud IS NOT NULL"
            " AND latitud BETWEEN -90 AND 90 AND longitud BETWEEN -180 AND 180)",
            name="domos_coordenadas_check",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    ubicacion: Mapped[Optional[str]] = mapped_column(Text)
    # Domo con datos sintéticos (tools/generar_datos_demo.py), no un domo real. Ningún
    # endpoint lo cambia, y la app y las exportaciones lo marcan siempre.
    es_demostracion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Antes en configuracion_sistema.parametros_energeticos. Definen los presets de R-13.
    bateria_tipo: Mapped[str] = mapped_column(Text, nullable=False, default="lifepo4", server_default="lifepo4")
    # NULL = sin batería configurada (la app no muestra la capacidad).
    bateria_capacidad_wh: Mapped[Optional[float]] = mapped_column(Float)
    perfil: Mapped[str] = mapped_column(Text, nullable=False, default="conservador", server_default="conservador")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Coordenadas para el clima de referencia (servicios/clima.py). Las dos o ninguna;
    # sin ellas el domo no tiene clima.
    latitud: Mapped[Optional[float]] = mapped_column(Float)
    longitud: Mapped[Optional[float]] = mapped_column(Float)

    fecha_registro = synonym("creado_en")
    asignaciones: Mapped[list["Asignacion"]] = relationship(back_populates="domo", cascade="all, delete-orphan")

    # Campos de DomoOut que v2 ya no guarda: el frontend no los usa (MAPEO.md).
    estado_domo = "activo"
    descripcion = None
    estado_conexion = None

    @property
    def propietario_id(self) -> Optional[int]:
        sesion = object_session(self)
        if sesion is None or self.id is None:
            return None
        return sesion.execute(
            select(Asignacion.usuario_id).where(Asignacion.domo_id == self.id, Asignacion.rol == ROL_DUENO)
        ).scalar()


class Asignacion(Base):
    """Quién puede ver un domo y con qué rol. Un solo 'dueno' por domo (índice único
    parcial en la base; en las pruebas lo cubre el backend)."""

    __tablename__ = "asignaciones"
    __table_args__ = (
        UniqueConstraint("usuario_id", "domo_id", name="asignaciones_usuario_domo_key"),
        CheckConstraint(_en("rol", (ROL_DUENO, ROL_ADMINISTRADOR)), name="asignaciones_rol_check"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=False)
    domo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("domos.id", ondelete="CASCADE"), nullable=False)
    rol: Mapped[str] = mapped_column(Text, nullable=False)

    usuario: Mapped["Usuario"] = relationship(back_populates="asignaciones")
    domo: Mapped["Domo"] = relationship(back_populates="asignaciones")


# Bloque 2 — Dispositivos ----------------------------------------------------

class Dispositivo(Base):
    __tablename__ = "dispositivos"
    __table_args__ = (
        UniqueConstraint("domo_id", "id_fisico", name="dispositivos_domo_id_id_fisico_key"),
        CheckConstraint(_en("tipo", ("toma", "bombillo")), name="dispositivos_tipo_check"),
        CheckConstraint(
            _en("categoria", ("esencial", "importante", "confort", "prescindible")), name="dispositivos_categoria_check"
        ),
        CheckConstraint(_en("estado", ("encendido", "apagado")), name="dispositivos_estado_check"),
        CheckConstraint("brillo BETWEEN 0 AND 100", name="dispositivos_brillo_rango_check"),
        CheckConstraint("tipo = 'bombillo' OR brillo IS NULL", name="dispositivos_brillo_solo_bombillo_check"),
        CheckConstraint("tipo <> 'bombillo' OR categoria = 'esencial'", name="dispositivos_bombillo_esencial_check"),
        # Migración alembic_v2 9d4f2a7b1c38. La lista de escenas NO va en la base: su
        # único lugar es servicios/iluminacion.py (y coincide con el firmware).
        CheckConstraint(_en("modo_luz", MODOS_LUZ), name="dispositivos_modo_luz_check"),
        CheckConstraint("tipo = 'bombillo' OR (modo_luz IS NULL AND escena IS NULL)", name="dispositivos_luz_solo_bombillo_check"),
        CheckConstraint("activo = (desactivado_en IS NULL)", name="dispositivos_desactivado_en_check"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    # RESTRICT: un dispositivo nunca se borra, se desactiva (activo = false).
    domo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("domos.id", ondelete="RESTRICT"), nullable=False)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # Bombillos: siempre 'esencial'. Define los umbrales y la prioridad de R-13, que se
    # calculan (servicios/categorias.py) y no se guardan.
    categoria: Mapped[str] = mapped_column(Text, nullable=False)
    # Dispositivo FÍSICO real (la toma Tuya que el ESP32 conmuta). NULL = simulado: nada
    # se publica por MQTT. Ver mqtt_publisher.py.
    id_fisico: Mapped[Optional[str]] = mapped_column(Text)
    ubicacion: Mapped[Optional[str]] = mapped_column(String(100))
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="apagado", server_default="apagado")
    brillo: Mapped[Optional[int]] = mapped_column(SmallInteger)
    color: Mapped[Optional[str]] = mapped_column(Text)
    temperatura_color: Mapped[Optional[str]] = mapped_column(Text)
    # Modo de luz del bombillo (blanco | color | escena; NULL = sin modo) y la escena
    # elegida. Ver servicios/iluminacion.py.
    modo_luz: Mapped[Optional[str]] = mapped_column(Text)
    escena: Mapped[Optional[str]] = mapped_column(Text)
    # False = desactivado: R-13 lo ignora y los listados lo ocultan por defecto.
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    # Cuándo se desactivó; NULL mientras está activo. Termina el tiempo que una toma
    # cuenta como cortada por R-13 en los reportes.
    desactivado_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Antes en estado_comunicacion (solo el último estado).
    conectado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    ultima_comunicacion: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    @property
    def estado_conexion(self) -> Optional[str]:
        """El texto que devolvía v1: 'conectado', 'desconectado' si alguna vez se
        comunicó, o None si nunca se registró nada."""
        if self.conectado:
            return "conectado"
        return "desconectado" if self.ultima_comunicacion is not None else None


class Accion(Base):
    """Encendidos y apagados de un dispositivo (antes historial_acciones). El domo sale
    por JOIN con dispositivos. El "quién" es texto sin FK: `actor_tipo` y `actor_ref`
    (el correo del usuario, o 'R-13')."""

    __tablename__ = "acciones"
    __table_args__ = (
        CheckConstraint(_en("accion", ("encender", "apagar")), name="acciones_accion_check"),
        CheckConstraint(_en("estado_anterior", ("encendido", "apagado")), name="acciones_estado_anterior_check"),
        CheckConstraint(_en("estado_nuevo", ("encendido", "apagado")), name="acciones_estado_nuevo_check"),
        CheckConstraint(_en("actor_tipo", ("sistema", ROL_DUENO, ROL_ADMINISTRADOR)), name="acciones_actor_tipo_check"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    dispositivo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dispositivos.id", ondelete="RESTRICT"), nullable=False
    )
    accion: Mapped[str] = mapped_column(Text, nullable=False)
    estado_anterior: Mapped[str] = mapped_column(Text, nullable=False)
    estado_nuevo: Mapped[str] = mapped_column(Text, nullable=False)
    actor_tipo: Mapped[str] = mapped_column(Text, nullable=False)
    actor_ref: Mapped[str] = mapped_column(Text, nullable=False)
    condicion: Mapped[Optional[str]] = mapped_column(Text)
    resultado: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


ACTOR_SISTEMA = "sistema"
ACTOR_REF_R13 = "R-13"


# Bloque 3 — Alertas y telemetría ----------------------------------------------

class Alerta(Base):
    __tablename__ = "alertas"
    __table_args__ = (
        CheckConstraint(
            _en("tipo", ("bateria_baja", "consumo_elevado", "generacion_nula", "dispositivo_desconectado", "sobrecarga")),
            name="alertas_tipo_check",
        ),
        CheckConstraint(_en("nivel", ("informativo", "advertencia", "critico")), name="alertas_nivel_check"),
        CheckConstraint(_en("estado", ("activa", "atendida", "cerrada")), name="alertas_estado_check"),
        CheckConstraint("estado <> 'atendida' OR fecha_atendida IS NOT NULL", name="alertas_atendida_con_fecha_check"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    domo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("domos.id", ondelete="CASCADE"), nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    nivel: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="activa", server_default="activa")
    variable: Mapped[Optional[str]] = mapped_column(Text)
    valor_detectado: Mapped[Optional[float]] = mapped_column(Float)
    limite: Mapped[Optional[float]] = mapped_column(Float)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    # id_fisico del dispositivo al que se refiere, si es de un dispositivo. Texto sin FK.
    id_fisico_ref: Mapped[Optional[str]] = mapped_column(Text)
    # R-13 solo cierra las alertas que creó él.
    creada_por_r13: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Cuándo pasó a 'atendida'. Se cambia con cambiar_estado.
    fecha_atendida: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Nombres de v1, los de AlertaOut y los filtros de la API.
    tipo_alerta = synonym("tipo")
    nivel_prioridad = synonym("nivel")
    estado_alerta = synonym("estado")
    descripcion = synonym("mensaje")
    variable_asociada = synonym("variable")
    valor_limite = synonym("limite")

    def cambiar_estado(self, estado: str) -> None:
        """Atender pone la fecha; cerrar la conserva; reabrir la borra, porque deja de
        estar atendida."""
        if estado == "atendida" and self.estado != "atendida":
            self.fecha_atendida = datetime.now(timezone.utc)
        elif estado == "activa":
            self.fecha_atendida = None
        self.estado = estado


class Medicion(Base):
    """Una fila por domo y bloque de 10 s (`fecha_hora` = inicio del bloque, en UTC).
    NULL = el sensor no respondió o ese tema no llegó en el bloque. La escribe
    servicios/mediciones.guardar_medicion: los valores instantáneos se reemplazan y la
    energía del intervalo se suma."""

    __tablename__ = "mediciones"
    __table_args__ = (
        UniqueConstraint("domo_id", "fecha_hora", name="mediciones_domo_fecha_key"),
        CheckConstraint(
            _en("estado_bateria", ("cargando", "descargando", "cargada", "en_reposo")),
            name="mediciones_estado_bateria_check",
        ),
        *(
            CheckConstraint(
                f"{columna} BETWEEN {LIMITES_TELEMETRIA[campo][0]!r} AND {LIMITES_TELEMETRIA[campo][1]!r}",
                name=f"mediciones_{columna}_rango",
            )
            for columna, campo in RANGO_DE_MEDICION.items()
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    domo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("domos.id", ondelete="CASCADE"), nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estado_bateria: Mapped[Optional[str]] = mapped_column(Text)
    soc_pct: Mapped[Optional[float]] = mapped_column(Float)
    voltaje_bateria_v: Mapped[Optional[float]] = mapped_column(Float)
    corriente_bateria_a: Mapped[Optional[float]] = mapped_column(Float)
    potencia_bateria_w: Mapped[Optional[float]] = mapped_column(Float)
    temperatura_bateria_c: Mapped[Optional[float]] = mapped_column(Float)
    voltaje_fv_v: Mapped[Optional[float]] = mapped_column(Float)
    corriente_fv_a: Mapped[Optional[float]] = mapped_column(Float)
    potencia_fv_w: Mapped[Optional[float]] = mapped_column(Float)
    energia_fv_wh: Mapped[Optional[float]] = mapped_column(Float)
    voltaje_ac_v: Mapped[Optional[float]] = mapped_column(Float)
    corriente_ac_a: Mapped[Optional[float]] = mapped_column(Float)
    potencia_consumo_w: Mapped[Optional[float]] = mapped_column(Float)
    energia_consumo_wh: Mapped[Optional[float]] = mapped_column(Float)
    frecuencia_hz: Mapped[Optional[float]] = mapped_column(Float)
    factor_potencia: Mapped[Optional[float]] = mapped_column(Float)


# Copia de alembic_v2 5a1c7e3f9b24 (RANGOS_CLIMA; tests/test_clima.py las compara).
RANGOS_CLIMA = {
    "nubosidad_pct": (0, 100),
    "radiacion_w_m2": (0, 2000),
    "precipitacion_mm": (0, 500),
    "temperatura_c": (-90, 70),
}


class Clima(Base):
    """Clima de referencia de un domo: lo que estima Open-Meteo para sus coordenadas a
    la hora `fecha_hora` (UTC). NO es una medición en el domo. Una fila por domo y hora
    de la estimación (la misma consulta repetida no duplica). NULL = Open-Meteo no
    entregó ese dato o vino fuera de rango. La escribe servicios/clima.py."""

    __tablename__ = "clima"
    __table_args__ = (
        UniqueConstraint("domo_id", "fecha_hora", name="clima_domo_fecha_key"),
        CheckConstraint("codigo_clima BETWEEN 0 AND 99", name="clima_codigo_clima_rango"),
        *(
            CheckConstraint(f"{columna} BETWEEN {minimo} AND {maximo}", name=f"clima_{columna}_rango")
            for columna, (minimo, maximo) in RANGOS_CLIMA.items()
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True)
    domo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("domos.id", ondelete="CASCADE"), nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperatura_c: Mapped[Optional[float]] = mapped_column(Float)
    nubosidad_pct: Mapped[Optional[float]] = mapped_column(Float)
    radiacion_w_m2: Mapped[Optional[float]] = mapped_column(Float)
    precipitacion_mm: Mapped[Optional[float]] = mapped_column(Float)
    codigo_clima: Mapped[Optional[int]] = mapped_column(SmallInteger)
    es_de_dia: Mapped[Optional[bool]] = mapped_column(Boolean)
    fuente: Mapped[str] = mapped_column(Text, nullable=False, default="open-meteo", server_default="open-meteo")
