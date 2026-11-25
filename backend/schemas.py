"""
Esquemas Pydantic (request/response) para los endpoints de autenticación
(R-1 / RF-01, RF-02), perfil de usuario (R-2), gestión de domos (R-3),
asignación de administradores a domos (R-4), monitoreo de generación
fotovoltaica (R-6), monitoreo de consumo energético (R-7), monitoreo del
sistema de almacenamiento (R-8), monitoreo de sensores (R-9), control /
estado de dispositivos (R-10, R-11, R-12), control energético — algoritmo
de supervivencia (R-13), historial de acciones (R-16), sistema de alertas
(R-15), estado de comunicación (R-17), gestión de administradores (R-18),
configuración del sistema (R-19), estado general del domo (R-5) y
visualización de históricos (R-14). Separados de models.py porque estos
representan la forma de los datos en el API, no el esquema de la base de
datos.
"""

from datetime import date, datetime, timezone
from typing import Annotated, Any, Iterable, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from servicios.iluminacion import VALORES_ESCENA, VALORES_TEMPERATURA_COLOR, normalizar, normalizar_color

# Valores de los catálogos estado_generacion y estado_bateria (migración
# b7d2c4e8a1f3). Los comparte mqtt_listener.py.
EstadoGeneracionValor = Literal["generando", "sin_generacion"]
EstadoBateriaValor = Literal["cargando", "descargando", "cargada", "en_reposo"]

# Catálogo categoria_dispositivo (migración c4a8e2f7d1b6).
CategoriaValor = Literal["esencial", "importante", "confort", "prescindible"]
TipoBateriaValor = Literal["lifepo4", "agm"]
PerfilControlValor = Literal["conservador", "equilibrado", "maximo_uso"]

# Las columnas numéricas son `real` en Postgres. Un número fuera de su alcance no
# se guarda: la base lo rechaza con un error, tanto si es enorme (1e300) como si es
# diminuto (1e-300). Estos límites son los valores físicamente creíbles para este
# sistema, con margen para el ruido de los sensores alrededor de cero. Los comparte
# mqtt_listener.py: la API responde 422 y el listener guarda NULL.
LIMITES_TELEMETRIA: dict[str, tuple[float, float]] = {
    "voltaje": (-10.0, 1000.0),
    "corriente": (-1000.0, 1000.0),
    "potencia": (-100_000.0, 100_000.0),
    "potencia_generada": (-100.0, 100_000.0),
    "potencia_consumida": (-100.0, 100_000.0),
    "energia_generada": (0.0, 1e9),
    "energia_consumida": (0.0, 1e9),
    "estado_carga_soc": (0.0, 100.0),
    "temperatura": (-50.0, 150.0),
    "valor": (-1e9, 1e9),
}
# Por debajo de esto, un valor se guarda como 0: es ruido, y `real` no lo representa.
VALOR_DIMINUTO = 1e-30


def sin_valores_diminutos(valor: float) -> float:
    return 0.0 if abs(valor) < VALOR_DIMINUTO else valor


def _numero_telemetria(campo: str):
    minimo, maximo = LIMITES_TELEMETRIA[campo]
    return Annotated[float, Field(ge=minimo, le=maximo), AfterValidator(sin_valores_diminutos)]


UmbralSoc = Annotated[float, Field(ge=0, le=100), AfterValidator(sin_valores_diminutos)]
BrilloValor = Annotated[int, Field(ge=0, le=100)]
NombreDispositivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NombrePersona = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
UbicacionDispositivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
IdFisicoDispositivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Solo los valores de servicios/iluminacion.py (DB-4). Al leer no se validan: un valor
# viejo fuera de la lista (p. ej. "string") no rompe DispositivoOut.
ColorBombillo = Annotated[str, AfterValidator(normalizar_color)]
TemperaturaColorBombillo = Annotated[str, AfterValidator(lambda valor: normalizar(valor, VALORES_TEMPERATURA_COLOR))]
EscenaBombillo = Annotated[str, AfterValidator(lambda valor: normalizar(valor, VALORES_ESCENA))]
ModoLuzValor = Literal["blanco", "color", "escena"]   # = servicios.iluminacion.MODOS_LUZ (lo comprueba un test)

# Campo que fija cada modo de luz: una orden no mezcla dos (contrato con el firmware).
CAMPO_DEL_MODO = {"blanco": "temperatura_color", "color": "color", "escena": "escena"}


def modo_de_los_campos(datos: dict) -> Optional[str]:
    """El modo que piden los campos de luz de un cuerpo (temperatura_color, color o escena,
    con valor), o None si no trae ninguno. ValueError si mezcla dos modos, o si `modo_luz`
    contradice el campo."""
    modos = [modo for modo, campo in CAMPO_DEL_MODO.items() if datos.get(campo) is not None]
    if len(modos) > 1:
        raise ValueError("No se pueden mezclar temperatura_color, color y escena en el mismo cambio: son de modos distintos.")
    deducido = modos[0] if modos else None
    pedido = datos.get("modo_luz")
    if pedido is not None and deducido is not None and pedido != deducido:
        raise ValueError(f"{CAMPO_DEL_MODO[deducido]} no corresponde al modo '{pedido}'.")
    return deducido

# Separación mínima entre apagado y reactivación de una toma: por debajo de
# ella, pequeñas variaciones del SoC la harían conmutar seguido.
BANDA_HISTERESIS_MIN_SOC = 5.0


def _cabe_en_bcrypt(contrasena: str) -> str:
    # bcrypt solo usa los primeros 72 bytes: el resto se ignoraría sin avisar.
    if len(contrasena.encode("utf-8")) > 72:
        raise ValueError("La contraseña no puede tener más de 72 bytes.")
    return contrasena


ContrasenaNueva = Annotated[str, Field(min_length=8), AfterValidator(_cabe_en_bcrypt)]


class EntradaApi(BaseModel):
    """Base de los cuerpos que recibe la API. Los números no finitos escritos
    como número los frena json_finito.py; esto frena los que llegan como texto
    ("NaN", "Infinity"), que Pydantic convertiría a float en cualquier campo sin
    rango. No se aplica a las respuestas: una fila vieja con NaN no debe impedir
    consultar el resto."""

    model_config = ConfigDict(allow_inf_nan=False)


def _rechazar_nulos(datos: Any, campos: Iterable[str]) -> Any:
    """Validador "before": en un cuerpo parcial, un campo ausente significa "no
    lo cambies", pero un null explícito se confundiría con ausente o llegaría
    a la base de datos. Se rechaza con 422."""
    if isinstance(datos, dict):
        nulos = sorted(campo for campo in campos if campo in datos and datos[campo] is None)
        if nulos:
            raise ValueError(f"Estos campos no pueden quedar vacíos: {', '.join(nulos)}.")
    return datos


class LoginRequest(EntradaApi):
    """Cuerpo esperado por POST /auth/login."""

    correo: EmailStr
    contrasena: str


class RegistroRequest(EntradaApi):
    """Cuerpo esperado por POST /auth/registro.

    No incluye `rol`: este endpoint solo crea cuentas de administrador
    (ver RF-02), el rol se fija en el servidor, no lo elige quien llama.

    `domo_id` (modelo v2, decisión 7 de docs/modelo_v2/MAPEO.md): el domo del dueño
    al que queda asignada la cuenta, en la misma transacción. Sin asignación, ningún
    dueño la vería.
    """

    nombre: NombrePersona
    apellido: Optional[str] = None
    correo: EmailStr
    contrasena: ContrasenaNueva
    telefono: Optional[str] = None
    domo_id: int


class TokenResponse(BaseModel):
    """Respuesta de POST /auth/login."""

    access_token: str
    token_type: str = "bearer"


class UsuarioOut(BaseModel):
    """Datos públicos de un usuario (nunca incluye contrasena_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    apellido: Optional[str] = None
    correo: EmailStr
    telefono: Optional[str] = None
    # v2 no guarda fotografía: siempre null. Se conserva por compatibilidad.
    fotografia: Optional[str] = None
    # Calculado: 'dueño' si la cuenta es dueña de algún domo. Solo para el menú.
    rol: str
    estado_cuenta: str
    debe_cambiar_contrasena: bool = False


class PerfilUpdateRequest(EntradaApi):
    """Cuerpo esperado por PUT /users/me (R-2 — Gestión del perfil).

    Deliberadamente NO incluye correo, rol ni estado_cuenta: un usuario no
    puede cambiar su propio correo, ascenderse de rol ni reactivarse a sí
    mismo desde este endpoint. Todos los campos son opcionales para
    permitir actualizar solo algunos. v2 no guarda fotografía.
    """

    nombre: Optional[str] = None
    apellido: Optional[str] = None
    telefono: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(datos, ("nombre",))


class DomoCreate(EntradaApi):
    """Cuerpo esperado por POST /domos (R-3).

    Deliberadamente NO incluye el dueño: la asignación 'dueno' se crea en el
    servidor para el usuario autenticado, nunca lo elige quien llama.
    """

    nombre: str
    ubicacion: Optional[str] = None


class DomoUpdate(EntradaApi):
    """Cuerpo esperado por PUT /domos/{id} (R-3). Todos los campos son
    opcionales para permitir actualizar solo algunos."""

    nombre: Optional[str] = None
    ubicacion: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(datos, ("nombre",))


class DomoOut(BaseModel):
    """Representación de un domo devuelta por la API.

    `propietario_id` sale de la asignación 'dueno'. `descripcion`, `estado_domo` y
    `estado_conexion` ya no se guardan (v2): van siempre null / 'activo'."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    propietario_id: Optional[int] = None
    nombre: str
    ubicacion: Optional[str] = None
    descripcion: Optional[str] = None
    estado_domo: str = "activo"
    estado_conexion: Optional[str] = None
    fecha_registro: datetime
    # Datos sintéticos: la app lo muestra como "Demostración · datos sintéticos".
    es_demostracion: bool = False
    # Coordenadas del clima de referencia (servicios/clima.py); null = sin clima.
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class AsignarAdministradorRequest(EntradaApi):
    """Cuerpo esperado por POST /domos/{domo_id}/administradores (R-4).

    Recibe el correo del administrador, no su id: el dueño no tiene por
    qué conocer los ids internos de usuarios.usuarios.
    """

    correo: EmailStr
    permisos_asignados: Optional[str] = None


class AsignacionUpdateRequest(EntradaApi):
    """Cuerpo esperado por PATCH /domos/{domo_id}/administradores/{id}
    (R-4). Ambos campos opcionales para permitir cambiar solo uno."""

    estado_asignacion: Optional[str] = None
    permisos_asignados: Optional[str] = None


class AdministradorDomoOut(BaseModel):
    """Representación de una asignación administrador↔domo, incluyendo
    nombre y correo del administrador para que el dueño no tenga que
    cruzar ids manualmente en la app."""

    id: int
    domo_id: int
    administrador_id: int
    nombre_administrador: str
    correo_administrador: EmailStr
    # v2 no guarda permisos ni fecha de asignación: siempre null.
    permisos_asignados: Optional[str] = None
    estado_asignacion: str
    fecha_asignacion: Optional[datetime] = None


class LecturaFotovoltaicaCreate(EntradaApi):
    """Cuerpo esperado por POST .../lecturas-fotovoltaicas (R-6).

    Por ahora este endpoint sirve para simular datos sin hardware real
    conectado. Más adelante será el propio backend quien inserte estas
    lecturas automáticamente al recibir telemetría MQTT del ESP32 (fuera
    del alcance de R-6); ese flujo reutilizará este mismo modelo de datos.

    `fecha_hora` es opcional: si no se envía, la asigna la base de datos
    (server_default=func.now() en models.py) al momento de la inserción.
    """

    voltaje: Optional[_numero_telemetria("voltaje")] = None
    corriente: Optional[_numero_telemetria("corriente")] = None
    potencia_generada: Optional[_numero_telemetria("potencia_generada")] = None
    energia_generada: Optional[_numero_telemetria("energia_generada")] = None
    estado_generacion: Optional[EstadoGeneracionValor] = None
    fecha_hora: Optional[datetime] = None


class LecturaFotovoltaicaOut(BaseModel):
    """Representación de una lectura fotovoltaica devuelta por la API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    sensor_id: Optional[int] = None
    voltaje: Optional[float] = None
    corriente: Optional[float] = None
    potencia_generada: Optional[float] = None
    energia_generada: Optional[float] = None
    estado_generacion: Optional[str] = None
    fecha_hora: datetime


class LecturaConsumoCreate(EntradaApi):
    """Cuerpo esperado por POST .../lecturas-consumo (R-7).

    `dispositivo_id` es opcional; si viene, debe ser un dispositivo de ese
    mismo domo (422 si no, ver routers/lecturas_consumo.py).

    `fecha_hora` es opcional: si no se envía, la asigna la base de datos
    (server_default=func.now() en models.py) al momento de la inserción.
    """

    dispositivo_id: Optional[int] = None
    voltaje: Optional[_numero_telemetria("voltaje")] = None
    corriente: Optional[_numero_telemetria("corriente")] = None
    potencia_consumida: Optional[_numero_telemetria("potencia_consumida")] = None
    energia_consumida: Optional[_numero_telemetria("energia_consumida")] = None
    fecha_hora: Optional[datetime] = None

    @model_validator(mode="after")
    def _sin_dispositivo(self):
        # v2 guarda el consumo total del domo (tabla mediciones), no el de cada dispositivo.
        if self.dispositivo_id is not None:
            raise ValueError("dispositivo_id ya no se admite: las lecturas de consumo son del domo completo.")
        return self


class LecturaConsumoOut(BaseModel):
    """Representación de una lectura de consumo devuelta por la API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    dispositivo_id: Optional[int] = None
    sensor_id: Optional[int] = None
    voltaje: Optional[float] = None
    corriente: Optional[float] = None
    potencia_consumida: Optional[float] = None
    energia_consumida: Optional[float] = None
    fecha_hora: datetime


class LecturaBateriaCreate(EntradaApi):
    """Cuerpo esperado por POST .../lecturas-bateria (R-8).

    Todos los campos de medición son opcionales, igual que en el modelo
    (models.py). `fecha_hora` es opcional: si no se envía, la asigna la
    base de datos (server_default=func.now()) al momento de la inserción.
    """

    voltaje: Optional[_numero_telemetria("voltaje")] = None
    corriente: Optional[_numero_telemetria("corriente")] = None
    potencia: Optional[_numero_telemetria("potencia")] = None
    estado_carga_soc: Optional[_numero_telemetria("estado_carga_soc")] = None
    temperatura: Optional[_numero_telemetria("temperatura")] = None
    estado_bateria: Optional[EstadoBateriaValor] = None
    fecha_hora: Optional[datetime] = None


class LecturaBateriaOut(BaseModel):
    """Representación de una lectura de batería devuelta por la API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    sensor_id: Optional[int] = None
    voltaje: Optional[float] = None
    corriente: Optional[float] = None
    potencia: Optional[float] = None
    estado_carga_soc: Optional[float] = None
    temperatura: Optional[float] = None
    estado_bateria: Optional[str] = None
    fecha_hora: datetime


class LecturaSensorCreate(EntradaApi):
    """Cuerpo esperado por POST .../lecturas-sensores (R-9).

    Todos los campos son opcionales, igual que en el modelo (models.py).
    `fecha_hora` es opcional: si no se envía, la asigna la base de datos
    (server_default=func.now()) al momento de la inserción.
    """

    tipo_sensor: Optional[str] = None
    variable_medida: Optional[str] = None
    valor: Optional[_numero_telemetria("valor")] = None
    unidad_medida: Optional[str] = None
    estado: Optional[str] = None
    fecha_hora: Optional[datetime] = None


class LecturaSensorOut(BaseModel):
    """Representación de una lectura de sensor devuelta por la API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    sensor_id: Optional[int] = None
    tipo_sensor: Optional[str] = None
    variable_medida: Optional[str] = None
    valor: Optional[float] = None
    unidad_medida: Optional[str] = None
    estado: Optional[str] = None
    fecha_hora: datetime


def validar_umbrales_finales(umbral_apagado: Optional[float], umbral_reactivacion: Optional[float]) -> None:
    """Coherencia del par de umbrales de una toma (R-13) tal como quedaría
    guardado: ambos o ninguno, y reactivación >= apagado + 5. La usan
    DispositivoCreate y el PATCH, que la aplica sobre el resultado de combinar
    el cuerpo con lo guardado: un PATCH con un solo umbral no puede dejar la
    banda invertida, que haría oscilar la toma en cada evaluación."""
    if (umbral_apagado is None) != (umbral_reactivacion is None):
        raise ValueError("umbral_apagado_soc y umbral_reactivacion_soc deben definirse juntos.")
    # round: en binario, 5.3 - 0.3 no da exactamente 5.
    if umbral_apagado is not None and round(umbral_reactivacion - umbral_apagado, 6) < BANDA_HISTERESIS_MIN_SOC:
        raise ValueError(
            f"umbral_reactivacion_soc debe ser al menos {BANDA_HISTERESIS_MIN_SOC:g} puntos "
            "mayor que umbral_apagado_soc."
        )


# Modelo v2 (decisión 5 de docs/modelo_v2/MAPEO.md): los umbrales se aceptan en los
# cuerpos solo para responder con este mensaje en vez de un error genérico.
UMBRALES_CALCULADOS = (
    "Los umbrales ya no se editan: se calculan con la categoría de la toma y el perfil y la "
    "batería del domo. Cambia la categoría o el perfil."
)


class DispositivoCreate(EntradaApi):
    """Cuerpo esperado por POST .../dispositivos (R-10/R-11). Exclusivo del dueño.

    `tipo` se restringe a 'bombillo'/'toma': son los dos tipos que cubren los
    requisitos actuales (docs/Requisitos/).

    `categoria`: por defecto 'confort' en tomas y 'esencial' en bombillos, que
    no admiten otra. El endpoint copia su preset (servicios/categorias.py) a
    prioridad y umbrales. `prioridad` ya no se recibe: se deriva de la categoría.

    Modo avanzado: una toma no esencial puede traer su propio par de umbrales,
    que reemplaza al del preset.

    Los campos que no aplican al tipo (`brillo`/`color`/`temperatura_color` en
    una toma, umbrales en un bombillo) y los desconocidos se rechazan con 422 en
    vez de ignorarse: quien los envía prefiere enterarse.

    `id_fisico` es opcional: sin él, el dispositivo queda simulado. Con él, una
    toma real se conmuta por MQTT, pero también hay que configurar la ESP32.

    `estado_conexion` y `ultima_comunicacion` no están aquí a propósito: los
    llenará la integración con el hardware.
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["bombillo", "toma"]
    nombre: NombreDispositivo
    ubicacion: Optional[UbicacionDispositivo] = None
    categoria: Optional[CategoriaValor] = None
    id_fisico: Optional[IdFisicoDispositivo] = None
    brillo: Optional[BrilloValor] = None
    color: Optional[ColorBombillo] = None
    temperatura_color: Optional[TemperaturaColorBombillo] = None
    umbral_apagado_soc: Optional[UmbralSoc] = None
    umbral_reactivacion_soc: Optional[UmbralSoc] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(datos, ("categoria", "umbral_apagado_soc", "umbral_reactivacion_soc"))

    @model_validator(mode="after")
    def _validar_campos_solo_de_bombillo(self):
        if self.tipo != "bombillo" and (
            self.brillo is not None or self.color is not None or self.temperatura_color is not None
        ):
            raise ValueError(
                "brillo, color y temperatura_color solo aplican a dispositivos tipo 'bombillo'."
            )
        return self

    @model_validator(mode="after")
    def _validar_campos_solo_de_toma(self):
        if self.umbral_apagado_soc is not None or self.umbral_reactivacion_soc is not None:
            raise ValueError(UMBRALES_CALCULADOS)
        if self.tipo != "toma" and self.categoria not in (None, "esencial"):
            raise ValueError("Un bombillo siempre es de categoría 'esencial'.")
        return self


class DispositivoUpdate(EntradaApi):
    """Cuerpo esperado por PATCH .../dispositivos/{id} (R-10/R-11).

    Cambiar `estado` en una toma con `id_fisico` no nulo sí conmuta la toma
    física real por MQTT; en cualquier otro caso el control sigue siendo
    simulado (solo la fila) — ver el docstring de routers/dispositivos.py.

    `estado` se restringe a 'encendido'/'apagado': son los dos valores que un
    usuario puede fijar manualmente. 'desconectado' y 'error' (catálogo en
    docs/Requisitos/Variables.md, sección 6) describen la comunicación real
    del dispositivo; no los fija un PATCH.

    Null explícito: se rechaza con 422 en los campos que no pueden quedar
    vacíos o en los que vaciarlos rompería algo. `ubicacion` e `id_fisico` sí
    lo admiten (borrar la ubicación, volver a simulado), igual que
    `brillo`/`color`/`temperatura_color`.

    Lo que depende del dispositivo guardado (su tipo, su categoría, el otro
    umbral) y del rol del usuario se valida en el endpoint, porque este esquema
    no lo conoce. `prioridad` ya no se recibe: se deriva de la categoría.

    Luz del bombillo: `modo_luz` (blanco | color | escena) y el campo de ese modo
    (`temperatura_color`, `color` o `escena`). Sin `modo_luz`, lo deduce el campo que
    venga. Un cuerpo no mezcla campos de dos modos, ni un `modo_luz` que contradiga el
    campo (422). `brillo` vale en blanco y en color.
    """

    model_config = ConfigDict(extra="forbid")

    estado: Optional[Literal["encendido", "apagado"]] = None
    brillo: Optional[BrilloValor] = None
    color: Optional[ColorBombillo] = None
    temperatura_color: Optional[TemperaturaColorBombillo] = None
    modo_luz: Optional[ModoLuzValor] = None
    escena: Optional[EscenaBombillo] = None
    nombre: Optional[NombreDispositivo] = None
    ubicacion: Optional[UbicacionDispositivo] = None
    categoria: Optional[CategoriaValor] = None
    activo: Optional[bool] = None
    id_fisico: Optional[IdFisicoDispositivo] = None
    umbral_apagado_soc: Optional[UmbralSoc] = None
    umbral_reactivacion_soc: Optional[UmbralSoc] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(
            datos,
            ("estado", "nombre", "categoria", "activo", "umbral_apagado_soc", "umbral_reactivacion_soc"),
        )

    @model_validator(mode="after")
    def _un_solo_modo_de_luz(self):
        modo_de_los_campos(self.model_dump(include={"modo_luz", "temperatura_color", "color", "escena"}))
        return self


class DispositivoOut(BaseModel):
    """Representación de un dispositivo devuelta por la API (R-12).

    Incluye `potencia`/`energia_consumida`/`estado_conexion`/
    `ultima_comunicacion` para dar la foto completa del dispositivo
    (RF-11), aunque ninguno de los endpoints de este router los escribe
    todavía — quedan en null hasta que exista la integración real con
    Tuya/ESP32 o la ingestión de telemetría (fuera del alcance de R-10,
    R-11, R-12).

    `id_fisico`: no nulo = el dispositivo es real y un cambio de `estado` en
    una toma se publica por MQTT hacia el hardware; NULL = simulado. El frontend
    lo usa para distinguir "Real" de "Simulado".

    Modelo v2: `prioridad`, `umbral_apagado_soc` y `umbral_reactivacion_soc` se
    CALCULAN con la categoría y la batería y el perfil del domo (no se guardan), y
    `personalizado` es siempre false. `potencia` y `energia_consumida` van null.
    `estado_conexion` sale de `dispositivos.conectado`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    id_fisico: Optional[str] = None
    tipo: str
    nombre: str
    ubicacion: Optional[str] = None
    activo: bool
    desactivado_en: Optional[datetime] = None
    categoria: str
    personalizado: bool = False
    estado: str
    brillo: Optional[int] = None
    color: Optional[str] = None
    temperatura_color: Optional[str] = None
    modo_luz: Optional[str] = None
    escena: Optional[str] = None
    prioridad: Optional[int] = None
    umbral_apagado_soc: Optional[float] = None
    umbral_reactivacion_soc: Optional[float] = None
    potencia: Optional[float] = None
    energia_consumida: Optional[float] = None
    estado_conexion: Optional[str] = None
    ultima_comunicacion: Optional[datetime] = None
    # Primacía humana vigente (DB-4, servicios/control_energetico.primacias_humanas):
    # la encendió una persona y R-13 la respeta hasta que el SoC llegue a
    # `primacia_humana_hasta_soc` (o la corta si baja de la reserva del domo). null
    # si no tiene. Se calcula, no se guarda.
    primacia_humana_hasta_soc: Optional[float] = None
    primacia_humana_desde: Optional[datetime] = None
    # De una toma real y activa, qué se sabe de su última orden (servicios/comunicacion.py,
    # confirmacion_de): pendiente (sin respuesta, < 60 s) | sin_confirmar (sin respuesta,
    # más de 60 s) | confirmada | sin_conexion (la ESP32 no pudo confirmarla). null en
    # simuladas, bombillos, desactivadas o sin órdenes ni respuestas. Se calcula.
    confirmacion: Optional[Literal["pendiente", "sin_confirmar", "confirmada", "sin_conexion"]] = None


class OpcionIluminacionOut(BaseModel):
    valor: str
    etiqueta: str
    # Solo en los colores: "#rrggbb" de su tono fijo, para mostrarlo en el selector.
    hex: Optional[str] = None


class PresetIluminacionOut(BaseModel):
    """Un preset de luz blanca. Se aplica con un PATCH de modo_luz="blanco",
    temperatura_color y, si no es null, brillo."""

    valor: str
    etiqueta: str
    temperatura_color: str
    brillo: Optional[int] = None


class OpcionesIluminacionOut(BaseModel):
    """GET /iluminacion/opciones: colores, temperaturas, escenas y presets del bombillo
    (servicios/iluminacion.py). `se_envian_al_bombillo`: true, el firmware los aplica."""

    colores: list[OpcionIluminacionOut]
    temperaturas_color: list[OpcionIluminacionOut]
    escenas: list[OpcionIluminacionOut]
    presets: list[PresetIluminacionOut]
    se_envian_al_bombillo: bool


class AccionControlEnergeticoOut(BaseModel):
    """Una acción ejecutada por el algoritmo de supervivencia sobre una
    toma puntual, dentro de la respuesta de POST .../control-energetico/evaluar.

    `umbral_aplicado` es el umbral efectivo (propio de la toma si lo
    define, si no la constante global) que se cruzó para disparar ESTA
    acción — de apagado si accion_realizada='apagar', de reactivación si
    accion_realizada='encender'. Con umbrales por toma ya no hay un único
    número que describa "el" umbral del domo, así que cada acción reporta
    el suyo (ver docstring del módulo en routers/control_energetico.py).
    """

    dispositivo_id: int
    nombre: str
    prioridad: Optional[int] = None
    accion_realizada: Literal["apagar", "encender"]
    umbral_aplicado: float
    # 'reserva': la había encendido una persona (primacía humana) y se cortó porque el
    # SoC bajó de la reserva de la batería; umbral_aplicado es entonces la reserva.
    # null: la regla normal.
    motivo: Optional[Literal["reserva"]] = None
    fecha_hora: datetime


class TomaRespetadaOut(BaseModel):
    """Una toma que esta evaluación habría cortado por su umbral, pero que R-13
    respeta porque la encendió una persona (primacía humana, DB-4)."""

    dispositivo_id: int
    nombre: str
    categoria: str
    umbral_apagado_soc: float
    # La primacía dura hasta que, después del encendido, el SoC llegue a este valor
    # (el umbral de reactivación de la toma)...
    hasta_soc: float
    # ...salvo que el SoC baje de la reserva: entonces se corta igual.
    reserva_soc: float
    encendida_desde: datetime


class EvaluarControlEnergeticoOut(BaseModel):
    """Respuesta de POST .../control-energetico/evaluar (R-13).

    No existe un `modo_evaluado` único para todo el domo: cada toma puede
    tener su propio umbral_apagado_soc/umbral_reactivacion_soc, así que en
    una misma llamada es normal que algunas tomas se apaguen y otras se
    reactiven a la vez (corte escalonado), o que no pase nada si ninguna
    cruzó su propio umbral. La respuesta se explica enteramente por
    `acciones` (puede venir vacía) — cada entrada trae su propio
    umbral_aplicado. `acciones` viene en el orden real de ejecución: todos
    los apagados primero (prioridad ascendente), luego todas las
    reactivaciones (prioridad descendente) — ver docstring del módulo.

    `umbral_soc_bajo`/`umbral_soc_recuperacion` son los umbrales GLOBALES
    (usados solo por las tomas que no definen los suyos propios), no "el"
    umbral aplicado a todo el domo.
    """

    domo_id: int
    estado_carga_soc: float
    umbral_soc_bajo: float
    umbral_soc_recuperacion: float
    acciones: list[AccionControlEnergeticoOut]
    # Las que se habrían cortado pero se respetan por primacía humana (DB-4).
    respetadas_por_primacia_humana: list[TomaRespetadaOut] = []


class TomaApagadaPorSistemaOut(BaseModel):
    """Una toma actualmente apagada por el algoritmo de supervivencia,
    dentro de la respuesta de GET .../control-energetico/estado."""

    dispositivo_id: int
    nombre: str
    categoria: str
    prioridad: Optional[int] = None
    apagada_desde: datetime


class TomaPorCortarOut(BaseModel):
    """Una toma encendida que R-13 apagará en la próxima evaluación."""

    dispositivo_id: int
    nombre: str
    categoria: str
    umbral_apagado_soc: float


class EstadoControlEnergeticoOut(BaseModel):
    """Respuesta de GET .../control-energetico/estado (R-13) — solo
    consulta, no ejecuta ninguna acción.

    `modo_actual` describe lo que R-13 YA hizo, no lo que el SoC anticipa: sin
    tomas apagadas por el sistema es 'normal'; si todas las apagadas son
    prescindibles, 'ahorro'; si no, 'supervivencia'. Por histéresis, una toma sigue
    contando hasta que una evaluación la reactive.

    `tomas_por_cortar` es lo que falta por pasar: las encendidas que con la última
    lectura ya cruzaron su umbral y se apagarán en la próxima evaluación. Puede
    durar poco (el listener evalúa cada 30 s) o quedarse así si nada está evaluando,
    y por eso no se mezcla con el modo.
    """

    domo_id: int
    ultima_lectura_bateria: Optional[LecturaBateriaOut] = None
    modo_actual: Literal["normal", "ahorro", "supervivencia"]
    tomas_apagadas_por_sistema: list[TomaApagadaPorSistemaOut]
    tomas_por_cortar: list[TomaPorCortarOut]


class HistorialAccionOut(BaseModel):
    """Representación de una fila de historial_acciones devuelta por
    GET /domos/{domo_id}/historial (R-16).

    Incluye `nombre_dispositivo` y `nombre_usuario` (resueltos con un JOIN
    en el endpoint, no por from_attributes) para que el frontend no tenga
    que cruzar ids manualmente — mismo criterio que AdministradorDomoOut
    en R-4. Ambos son opcionales porque `dispositivo_id`/`usuario_id`
    pueden ser null en la fila (p. ej. una acción del sistema no tiene
    usuario; ver rol='sistema' en routers/control_energetico.py).
    """

    id: int
    domo_id: int
    dispositivo_id: Optional[int] = None
    nombre_dispositivo: Optional[str] = None
    usuario_id: Optional[int] = None
    nombre_usuario: Optional[str] = None
    rol: Optional[str] = None
    accion_realizada: Optional[str] = None
    valor_anterior: Optional[str] = None
    valor_nuevo: Optional[str] = None
    condicion_control: Optional[str] = None
    resultado: Optional[str] = None
    fecha_hora: datetime


class AlertaCreate(EntradaApi):
    """Cuerpo esperado por POST /domos/{domo_id}/alertas (R-15).

    Por ahora esto sirve para simular alertas sin tener generación
    automática real todavía — más adelante será lógica que revise las
    lecturas entrantes o el algoritmo de supervivencia (R-13) y cree
    alertas sola cuando algo cruce un límite (trabajo futuro, fuera del
    alcance de R-15; ver TODO en routers/alertas.py).

    Modelo v2: `tipo_alerta`, `nivel_prioridad` y `descripcion` son obligatorios
    (la tabla alertas no los admite vacíos).

    `tipo_alerta` y `nivel_prioridad` se restringen a los catálogos ya
    decididos en docs/Requisitos/Variables.md (sección 11), para no dejar
    que se cuelen valores libres que ni esta generación manual ni la
    futura automática reconocerían. `estado_alerta` NO está aquí: siempre
    queda 'activa' al crear (default del modelo, ver models.py); cambiarla
    es cosa de PATCH (AlertaUpdate). `fecha_hora` es opcional: si no se
    envía, la asigna la base de datos (server_default=func.now()).
    """

    tipo_alerta: Literal["bateria_baja", "consumo_elevado", "generacion_nula", "dispositivo_desconectado", "sobrecarga"]
    descripcion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    nivel_prioridad: Literal["informativo", "advertencia", "critico"]
    variable_asociada: Optional[str] = None
    # id_fisico del dispositivo al que se refiere la alerta, si es de uno. Sin FK.
    id_fisico_ref: Optional[str] = None
    valor_detectado: Optional[_numero_telemetria("valor")] = None
    valor_limite: Optional[_numero_telemetria("valor")] = None
    fecha_hora: Optional[datetime] = None


class AlertaUpdate(EntradaApi):
    """Cuerpo esperado por PATCH /domos/{domo_id}/alertas/{id} (R-15).

    Solo permite cambiar `estado_alerta` (p. ej. marcarla como atendida o
    cerrada) — es literalmente lo único que este endpoint hace, así que,
    a diferencia de otros *Update de este archivo, el campo es obligatorio
    en vez de opcional (no hay nada más que actualizar "parcialmente").
    Catálogo según docs/Requisitos/Variables.md, sección 11.
    """

    estado_alerta: Literal["activa", "atendida", "cerrada"]


class AlertaOut(BaseModel):
    """Representación de una alerta devuelta por la API (R-15)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domo_id: int
    tipo_alerta: Optional[str] = None
    descripcion: Optional[str] = None
    nivel_prioridad: Optional[str] = None
    variable_asociada: Optional[str] = None
    valor_detectado: Optional[float] = None
    valor_limite: Optional[float] = None
    estado_alerta: str
    fecha_hora: datetime
    fecha_atendida: Optional[datetime] = None
    id_fisico_ref: Optional[str] = None
    creada_por_r13: bool = False


class EstadoComunicacionCreate(EntradaApi):
    """Cuerpo esperado por POST .../estado-comunicacion (R-17).

    Todos los campos son opcionales, igual que en el modelo (models.py).
    No hay catálogos documentados todavía para `estado_conexion`/
    `estado_mqtt` (el PDF de requisitos marca "estado MQTT" como
    Pendiente), así que se dejan como texto libre por ahora.

    Por ahora esto simula lo que la integración real con Tuya/ESP32
    reportará más adelante (mismo espíritu que "control simulado" en
    routers/dispositivos.py, R-10/R-11) — no hay generación automática
    real todavía.

    `ultima_comunicacion` es opcional: si no se envía, la asigna la base
    de datos (server_default=func.now()) al momento de la inserción.
    """

    estado_conexion: Optional[str] = None
    senal: Optional[str] = None
    identificador_dispositivo: Optional[str] = None
    estado_mqtt: Optional[str] = None
    ultima_comunicacion: Optional[datetime] = None


class EstadoComunicacionOut(BaseModel):
    """Representación de un registro de estado de comunicación devuelto
    por la API (R-17)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    dispositivo_id: int
    estado_conexion: Optional[str] = None
    senal: Optional[str] = None
    identificador_dispositivo: Optional[str] = None
    estado_mqtt: Optional[str] = None
    ultima_comunicacion: datetime


class DomoDeAdministradorOut(BaseModel):
    """Una asignación de un administrador a un domo del dueño autenticado."""

    domo_id: int
    nombre_domo: str
    estado_asignacion: str


class AdministradorPropioOut(BaseModel):
    """Un administrador en GET /administradores (R-18). Nunca lleva hashes ni tokens.

    `domos` solo incluye los del dueño autenticado, también las asignaciones
    revocadas (con su estado). `cuenta_propia` es True si este dueño creó la cuenta:
    solo entonces puede editarla o desactivarla."""

    id: int
    nombre: str
    apellido: Optional[str] = None
    correo: EmailStr
    telefono: Optional[str] = None
    estado_cuenta: str
    fecha_creacion: datetime
    cuenta_propia: bool
    domos: list[DomoDeAdministradorOut]


class RestablecerContrasenaRequest(EntradaApi):
    """Cuerpo de POST /administradores/{id}/restablecer-contrasena."""

    contrasena_temporal: ContrasenaNueva


class CambiarContrasenaRequest(EntradaApi):
    """Cuerpo de POST /users/me/contrasena."""

    contrasena_actual: str
    contrasena_nueva: ContrasenaNueva


class AdministradorUpdate(EntradaApi):
    """Cuerpo de PATCH /administradores/{id}. El correo no se cambia: identifica la
    cuenta. `estado_cuenta`='inactivo' bloquea el acceso de inmediato."""

    model_config = ConfigDict(extra="forbid")

    nombre: Optional[NombrePersona] = None
    apellido: Optional[str] = None
    telefono: Optional[str] = None
    estado_cuenta: Optional[Literal["activo", "inactivo"]] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(datos, ("nombre", "estado_cuenta"))


class BateriaConfig(EntradaApi):
    """Batería del domo, guardada en parametros_energeticos bajo la clave
    "bateria". `tipo` decide los presets de categoría (servicios/categorias.py)."""

    tipo: TipoBateriaValor
    capacidad_wh: float = Field(gt=0, allow_inf_nan=False)


class ConfiguracionSistemaUpdate(EntradaApi):
    """Cuerpo esperado por PUT /domos/{domo_id}/configuracion (R-19).

    Todos los campos son opcionales (merge parcial vía exclude_unset —
    mismo criterio que DomoUpdate/DispositivoUpdate en este proyecto,
    pese al verbo PUT: ver routers/configuracion.py). Actualiza o crea
    (upsert) la única fila de configuración de ese domo.

    `parametros_control`, `limites_alerta`, `prioridades_carga` y
    `configuracion_dispositivos` son texto libre — ningún requisito ni
    consumidor define su estructura todavía, a diferencia de
    `parametros_energeticos` (ver abajo).

    Los umbrales globales no pueden quedar por debajo de la reserva de la batería
    (422); sin valores guardados, siguen el preset confort (ver
    servicios/categorias.py, umbrales_globales_de).

    `umbral_soc_bajo`/`umbral_soc_recuperacion`: los umbrales GLOBALES del
    algoritmo de supervivencia (R-13) para este domo — el respaldo que
    usan las tomas que no definen su propio `umbral_apagado_soc`/
    `umbral_reactivacion_soc` (R-13 por-dispositivo, sin cambios). Se
    guardan serializados como JSON dentro de la columna TEXT
    `parametros_energeticos`, bajo la clave "control_energetico" — ver el
    docstring de routers/configuracion.py para el formato exacto y la
    justificación. Pueden enviarse por separado (el merge parcial aplica
    también a este par); si al combinarse con lo ya guardado ambos quedan
    definidos, se valida que reactivación > apagado (422 si no).

    `bateria`: tipo y capacidad, ambos obligatorios si se envía. También va en
    `parametros_energeticos`, sin tocar las demás claves. `perfil_control` se guarda
    igual. Si cambia el tipo de batería o el perfil, las tomas que seguían el preset
    anterior pasan al nuevo; las personalizadas no se tocan (ver
    ConfiguracionSistemaOut).

    Estos cuatro campos no admiten null explícito (422).
    """

    parametros_control: Optional[str] = None
    limites_alerta: Optional[str] = None
    prioridades_carga: Optional[str] = None
    configuracion_dispositivos: Optional[str] = None
    umbral_soc_bajo: Optional[UmbralSoc] = None
    umbral_soc_recuperacion: Optional[UmbralSoc] = None
    bateria: Optional[BateriaConfig] = None
    perfil_control: Optional[PerfilControlValor] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_nulos(cls, datos: Any) -> Any:
        return _rechazar_nulos(datos, ("umbral_soc_bajo", "umbral_soc_recuperacion", "bateria", "perfil_control"))


class ConfiguracionSistemaOut(BaseModel):
    """Respuesta de GET/PUT /domos/{domo_id}/configuracion (R-19).

    `es_default` es True cuando el domo no tiene ninguna fila guardada en
    `configuracion_sistema` todavía — en ese caso esta respuesta muestra
    valores por defecto razonables SIN haber creado nada en la base de
    datos (`fecha_modificacion` queda None).

    `umbral_soc_bajo`/`umbral_soc_recuperacion` son siempre los valores
    EFECTIVOS: los guardados si existen, si no los de respaldo globales
    de routers/control_energetico.py — mismo patrón "efectivo" que ya
    existe en R-13 por-dispositivo, aplicado aquí a nivel de domo.
    """

    domo_id: int
    es_default: bool
    parametros_control: Optional[str] = None
    limites_alerta: Optional[str] = None
    prioridades_carga: Optional[str] = None
    configuracion_dispositivos: Optional[str] = None
    umbral_soc_bajo: float
    umbral_soc_recuperacion: float
    bateria: Optional[BateriaConfig] = None
    perfil_control: PerfilControlValor
    fecha_modificacion: Optional[datetime] = None
    # Solo en la respuesta del PUT; en cero si este PUT no cambió la batería ni el perfil.
    tomas_recalculadas: Optional[int] = None
    tomas_personalizadas: Optional[int] = None


class PresetCategoriaOut(BaseModel):
    """Lo que se copia a un dispositivo al elegir esta categoría. En 'esencial'
    los umbrales son null y `corte_automatico` es False: R-13 nunca la corta."""

    categoria: str
    umbral_apagado_soc: Optional[float] = None
    umbral_reactivacion_soc: Optional[float] = None
    prioridad: int
    corte_automatico: bool


class PerfilPresetsOut(BaseModel):
    """Los presets de un perfil de control para la batería del domo."""

    perfil: PerfilControlValor
    categorias: list[PresetCategoriaOut]


class CategoriasDomoOut(BaseModel):
    """Respuesta de GET /domos/{domo_id}/categorias. `tipo_bateria` es el
    configurado, o 'lifepo4' si el domo no tiene uno
    (`bateria_configurada` = False). `categorias` son los presets del perfil
    actual; `perfiles`, los de los tres, para la vista previa."""

    domo_id: int
    tipo_bateria: TipoBateriaValor
    bateria_configurada: bool
    reserva_soc: float
    # Reserva de cada tipo de batería, para explicarlos antes de elegir uno.
    reservas_por_bateria: dict[str, float]
    perfil: PerfilControlValor
    categorias: list[PresetCategoriaOut]
    perfiles: list[PerfilPresetsOut]


class EstadoGeneralDomoOut(BaseModel):
    """Respuesta de GET /domos/{domo_id}/estado-general (R-5) — resumen de
    solo lectura que agrega datos ya existentes en otras tablas; no crea
    ni modifica nada.

    Cada campo de lectura (`potencia_generada`, `potencia_consumida`,
    `estado_bateria`, `estado_carga_soc`) es None si ese domo todavía no
    tiene ninguna lectura del tipo correspondiente — no es un error, es el
    estado real de un domo recién creado o sin telemetría reciente.

    `dispositivos_conectados` cuenta los dispositivos de ese domo con
    `estado_conexion == 'conectado'` — ver el docstring de
    routers/estado_general.py para la justificación de ese valor exacto
    (Variables.md no define un catálogo de `estado_conexion`, a
    diferencia del catálogo de `estado` que sí existe).

    `fecha_actualizacion` es la más reciente entre las tres lecturas que
    sí existan (fotovoltaica/consumo/batería); None si el domo no tiene
    ninguna lectura de ningún tipo todavía.
    """

    domo_id: int
    nombre_domo: str
    estado_domo: str
    estado_conexion: Optional[str] = None
    potencia_generada: Optional[float] = None
    potencia_consumida: Optional[float] = None
    estado_bateria: Optional[str] = None
    estado_carga_soc: Optional[float] = None
    dispositivos_conectados: int
    dispositivos_total: int
    alertas_activas: int
    fecha_actualizacion: Optional[datetime] = None


class HistoricoDomoOut(BaseModel):
    """Respuesta de GET /domos/{domo_id}/historico (R-14) — agregados de
    solo lectura sobre lecturas_fotovoltaicas y lecturas_consumo en un
    rango de fechas; no crea ni modifica nada.

    El catálogo de variables de R-14 (ver PDF de requisitos) es
    exactamente: energía generada, energía consumida, potencia máxima y
    potencia promedio — no incluye ninguna variable de batería, así que
    esta respuesta tampoco (ver docstring de routers/historico.py).

    Todos los campos numéricos son None cuando no hay ninguna lectura del
    tipo correspondiente en el rango pedido — un SUM/MAX/AVG sobre cero
    filas es NULL en SQL, y devolverlo como 0 sería afirmar "se generó/
    consumió exactamente cero" en vez de "no hay datos para este período",
    que es la realidad. Mismo criterio que los demás endpoints de este
    proyecto (p. ej. GET .../ultima devuelve 404 en vez de inventar una
    lectura).
    """

    domo_id: int
    fecha_desde: datetime
    fecha_hasta: datetime
    energia_generada_total: Optional[float] = None
    energia_consumida_total: Optional[float] = None
    potencia_generada_maxima: Optional[float] = None
    potencia_generada_promedio: Optional[float] = None
    potencia_consumida_maxima: Optional[float] = None
    potencia_consumida_promedio: Optional[float] = None


class ReporteDiaOut(BaseModel):
    """Un día del rango, en hora de Bogotá. Sin datos ese día, null."""

    fecha: date
    kwh_generados: Optional[float] = None
    kwh_consumidos: Optional[float] = None
    soc_minimo: Optional[float] = None
    soc_maximo: Optional[float] = None
    soc_promedio: Optional[float] = None


class ReporteHoraOut(BaseModel):
    """Promedio de las lecturas de esa hora del día, a lo largo del rango."""

    hora: int
    generacion_w: Optional[float] = None
    consumo_w: Optional[float] = None


class ReporteCorteCategoriaOut(BaseModel):
    categoria: str
    cortes: int


class ReporteCorteTomaOut(BaseModel):
    dispositivo_id: int
    nombre: str
    categoria: str
    # False si la toma está desactivada: su corte terminó al desactivarla.
    activo: bool = True
    cortes: int
    horas_apagada: float


class ReporteRolOut(BaseModel):
    rol: str
    total: int


class ReporteAccionesOut(BaseModel):
    automaticas: int
    manuales: int
    por_rol: list[ReporteRolOut]


class ReporteAlertaTipoOut(BaseModel):
    tipo_alerta: Optional[str] = None
    nivel_prioridad: Optional[str] = None
    total: int


class ReporteAlertasOut(BaseModel):
    """`minutos_medios_hasta_atendida` solo cuenta las `alertas_con_tiempo`: las
    atendidas antes de que se guardara esa fecha no la tienen."""

    total: int
    por_tipo: list[ReporteAlertaTipoOut]
    minutos_medios_hasta_atendida: Optional[float] = None
    alertas_con_tiempo: int


class ReporteResumenOut(BaseModel):
    """Respuesta de GET /domos/{id}/reportes/resumen. Reglas en servicios/reportes.py."""

    domo_id: int
    desde: date
    hasta: date
    zona_horaria: str
    kwh_generados: float
    kwh_consumidos: float
    balance_kwh: float
    # Domo de demostración: todos los datos del reporte son sintéticos.
    es_demostracion: bool = False
    tipo_bateria: TipoBateriaValor
    reserva_soc: float
    horas_bajo_reserva: float
    dias: list[ReporteDiaOut]
    perfil_horario: list[ReporteHoraOut]
    cortes_por_categoria: list[ReporteCorteCategoriaOut]
    cortes_por_toma: list[ReporteCorteTomaOut]
    acciones: ReporteAccionesOut
    alertas: ReporteAlertasOut


class ClimaOut(BaseModel):
    """Una estimación de Open-Meteo para las coordenadas del domo (servicios/clima.py).
    NO es una medición en el domo. `fecha_hora` en UTC; None = dato que no llegó."""

    model_config = ConfigDict(from_attributes=True)

    fecha_hora: datetime
    temperatura_c: Optional[float] = None
    nubosidad_pct: Optional[float] = None
    radiacion_w_m2: Optional[float] = None
    precipitacion_mm: Optional[float] = None
    codigo_clima: Optional[int] = None
    es_de_dia: Optional[bool] = None
    fuente: str

    @field_validator("fecha_hora")
    @classmethod
    def _en_utc(cls, valor: datetime) -> datetime:
        # SQLite devuelve la fecha sin zona: es UTC.
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


class ClimaActualOut(BaseModel):
    """Respuesta de GET /domos/{id}/clima/actual. Sin clima, `disponible` es False y
    `motivo` dice por qué: 'sin_coordenadas' (el domo no tiene latitud y longitud) o
    'sin_datos' (todavía no hay ninguna consulta guardada)."""

    domo_id: int
    disponible: bool
    motivo: Optional[Literal["sin_coordenadas", "sin_datos"]] = None
    clima: Optional[ClimaOut] = None
    # Segundos desde la hora de la estimación.
    antiguedad_s: Optional[int] = None


class AutonomiaRecienteOut(BaseModel):
    """Autonomía con la potencia media de la batería de los últimos 15 min (o de los
    minutos con datos, si hay menos). Ver servicios/energia_reciente.py.

    `tipo`: horas | mas_del_tope | cargando | reserva | sin_capacidad | pocos_datos |
    sin_datos. `potencia_media_w`: potencia neta media de la batería (+ carga, − descarga).
    `carga_dc_media_w`: lo que consumiría sin sol (inversor + consumo propio)."""

    tipo: Literal["horas", "mas_del_tope", "cargando", "reserva", "sin_capacidad", "pocos_datos", "sin_datos"]
    horas: Optional[float] = None
    horas_sin_sol: Optional[float] = None
    sin_sol_supera_tope: bool = False
    potencia_media_w: Optional[float] = None
    carga_dc_media_w: Optional[float] = None
    minutos: float
    ventana_min: float
    tope_horas: float


class FlujoRecienteOut(BaseModel):
    """Promedios de los últimos 60 s (null = sin dato en ese minuto)."""

    generada: Optional[float] = None
    consumida: Optional[float] = None
    bateria: Optional[float] = None
    # Pérdidas del inversor (DC de paneles y batería -> AC de las cargas), con esos promedios:
    # servicios/energia_reciente.perdidas_inversor. Eficiencia null con entrada < 20 W.
    entrada_inversor_w: Optional[float] = None
    perdidas_w: Optional[float] = None
    eficiencia_pct: Optional[float] = None


class EnergiaRecienteOut(BaseModel):
    """Respuesta de GET /domos/{id}/energia-reciente. Las ventanas terminan en
    `fecha_ultima` (la última medición con alguna potencia); si tiene más de 120 s,
    `vigente` es False y no hay autonomía ni flujo."""

    domo_id: int
    vigente: bool
    fecha_ultima: Optional[datetime] = None
    autonomia: Optional[AutonomiaRecienteOut] = None
    flujo: Optional[FlujoRecienteOut] = None

    @field_validator("fecha_ultima")
    @classmethod
    def _en_utc(cls, valor: Optional[datetime]) -> Optional[datetime]:
        if valor is None:
            return None
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


# ---------------------------------------------------------------- Recalibración guiada del SoC
# Acciones de domo/{id}/comando/soc y estados de domo/{id}/estado/soc (los publica el firmware,
# firmware/domo_nodo/soc_recalibracion.cpp; tests/test_comandos_mqtt.py compara los dos).
AccionRecalibracionSoc = Literal["iniciar", "confirmar", "cancelar"]
EstadoRecalibracionSoc = Literal["inactiva", "esperando", "lista", "aplicada", "cancelada", "vencida", "rechazada"]
# Además, solo del backend: "sin_sesion" (nunca llegó un estado) e "interrumpida" (un
# esperando/lista sin noticias: la ESP32 se reinició o perdió la conexión).
EstadoRecalibracionSocOut = Literal[
    "sin_sesion", "interrumpida", "inactiva", "esperando", "lista", "aplicada", "cancelada", "vencida", "rechazada",
]


class SocRecalibracionIn(BaseModel):
    """POST /domos/{id}/soc-recalibracion. Solo la acción: el dueño NUNCA manda un valor de
    SoC (un campo extra, p. ej. "soc", es un 422)."""

    model_config = ConfigDict(extra="forbid")

    accion: AccionRecalibracionSoc


class SocRecalibracionAccionOut(BaseModel):
    domo_id: int
    accion: AccionRecalibracionSoc
    enviado: bool


class SocRecalibracionOut(BaseModel):
    """GET /domos/{id}/soc-recalibracion: el último estado que publicó la ESP32."""

    domo_id: int
    estado: EstadoRecalibracionSocOut
    minutos_ok: Optional[float] = None
    requeridos: Optional[float] = None
    motivo: Optional[str] = None
    soc_actual: Optional[float] = None
    soc_propuesto: Optional[float] = None
    voltaje: Optional[float] = None
    # Cuándo lo publicó la ESP32 (su "t") o, sin hora, cuándo llegó al backend.
    actualizado: Optional[datetime] = None

    @field_validator("actualizado")
    @classmethod
    def _en_utc(cls, valor: Optional[datetime]) -> Optional[datetime]:
        if valor is None:
            return None
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)
