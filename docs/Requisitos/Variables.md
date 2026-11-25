# Variables del sistema

## 1. Descripción

Este documento define las variables que deben ser consideradas para el
desarrollo de APP-GLAMPING.

Las variables se agrupan en:

- Variables de usuario.
- Variables del domo.
- Asignación de administradores a domos.
- Variables energéticas.
- Variables de dispositivos.
- Variables de cargas controlables.
- Variables específicas por tipo de dispositivo.
- Variables de control (historial de acciones).
- Variables de comunicación.
- Variables de alertas.
- Variables de configuración del sistema.
- Mensajería MQTT (topics y estructura de payloads).
- Frecuencias de muestreo, publicación y almacenamiento.

---

## 2. Variables de usuario

| Variable | Tipo | Descripción |
|---|---|---|
| user_id | UUID | Identificador único del usuario en la tabla de perfiles |
| auth_id | UUID | Identificador del usuario en Supabase Auth (credenciales) |
| nombre | String | Nombre del usuario |
| correo | String | Correo electrónico |
| rol | Enum | Tipo de usuario |
| estado | Boolean | Estado de la cuenta |

`auth_id` es la referencia al usuario creado en Supabase Auth (correo y
contraseña); esta tabla solo guarda el perfil y el rol, conforme al
mecanismo descrito en `Usuariosypermisos.md` (sección 6).

---

## 3. Variables del domo

| Variable | Tipo | Descripción |
|---|---|---|
| domo_id | UUID | Identificador del domo |
| nombre_domo | String | Nombre asignado al domo |
| estado | Boolean | Estado del domo |
| propietario_id | UUID | Usuario propietario |

La asignación de administradores a un domo no se maneja como un campo único
dentro del domo, ya que un administrador puede tener varios domos asignados.
Esa relación se define en la siguiente sección.

---

## 4. Asignación de administradores a domos

| Variable | Tipo | Descripción |
|---|---|---|
| asignacion_id | UUID | Identificador de la asignación |
| domo_id | UUID | Domo asignado |
| administrador_id | UUID | Administrador asignado |
| permisos | Enum | Nivel de permisos otorgados sobre ese domo |
| fecha_asignacion | Timestamp | Fecha en que se realizó la asignación |

**Decisión:** para el alcance actual, el rol de administrador tiene un
conjunto fijo de capacidades (definido en `Usuariosypermisos.md`) que no
varía de un domo a otro. Por eso `permisos` se define con un único valor
válido por ahora: `estandar` (acceso operativo completo del rol
administrador sobre ese domo). El campo se conserva en el modelo, en vez de
eliminarse, para no romper el diseño si en el futuro se necesita
granularidad por domo (por ejemplo, un administrador con acceso de solo
lectura a un domo específico).

---

## 5. Variables energéticas

| Variable | Unidad / Tipo | Descripción |
|---|---|---|
| medicion_id | UUID | Identificador único de la medición |
| domo_id | UUID | Domo al que pertenece la medición |
| dispositivo_id | UUID | Dispositivo o sensor que originó la medición |
| origen | Enum | Fuente de la medición: generacion / consumo / bateria |
| voltaje | V | Voltaje medido |
| corriente | A | Corriente medida |
| potencia | W | Potencia instantánea |
| energia | Wh | Energía acumulada |
| fecha_hora | Timestamp | Momento de la medición |

El campo `origen` es necesario para poder separar en la aplicación las
pantallas de generación fotovoltaica, consumo y estado de batería, que se
alimentan de la misma tabla pero representan magnitudes distintas.

**Decisión — variables eléctricas disponibles:** se limitan a voltaje RMS,
corriente RMS, potencia activa y energía acumulada, que son exactamente las
que entrega el sensor HLW8012 seleccionado (ver `SelCOM.pdf`, Componente 2).
No se incluyen potencia aparente, potencia reactiva ni factor de potencia
en esta fase, porque el HLW8012 no las expone de forma directa y ningún
requisito actual las exige.

---

## 6. Variables de dispositivos

| Variable | Tipo | Descripción |
|---|---|---|
| dispositivo_id | UUID | Identificador del dispositivo |
| nombre | String | Nombre del dispositivo |
| tipo | Enum | Tipo de dispositivo |
| estado | Enum | Estado actual del dispositivo |
| domo_id | UUID | Domo al que pertenece |
| id_fisico | String (opcional) | Identificador del dispositivo físico real que representa la fila. NULL = dispositivo simulado |
| ubicacion | String, máx. 100 (opcional) | Dónde está dentro del domo |
| activo | Boolean | `false` = desactivado, sin borrar la fila (sección 17) |
| categoria | Enum | Qué tan protegido está frente al control energético (sección 17) |
| prioridad | Integer | Derivada de la categoría; ordena los cortes de una misma evaluación |
| umbral_apagado_soc, umbral_reactivacion_soc | Float (opcionales) | Umbrales de SoC de la toma para el control energético |

**Decisión — catálogo de `estado`:**

- `encendido`
- `apagado`
- `desconectado` (sin comunicación con el sistema)
- `error` (responde pero reporta una falla)

**Decisión — `id_fisico` distingue lo real de lo simulado**
(columna `dispositivos.id_fisico`, Text nullable, agregada en la
migración `e1857d5e5e9d`):

La tabla `dispositivos` contiene a la vez dispositivos de prueba (creados
desde `/docs` para validar la lógica del backend) y dispositivos físicos
reales del domo. `id_fisico` es lo que los separa:

- **No nulo** — la fila corresponde a un dispositivo físico real (por
  ejemplo, la toma Tuya que conmuta el ESP32). Su valor es el
  identificador de ese dispositivo, y viaja dentro del payload del
  comando MQTT (ver 13.2).
- **NULL** (el caso por defecto, y el de todas las filas que existían
  antes de la migración) — dispositivo **simulado**: los endpoints
  siguen actualizando su fila y registrando la acción en
  `historial_acciones` igual que siempre, pero **no se publica ningún
  comando MQTT**, así que no mueve hardware.

Lo fija el dueño al crear el dispositivo o con `PATCH .../dispositivos/{id}`; es
único dentro del domo. Una toma real también requiere configurar la ESP32.

---

## 7. Variables de cargas controlables

| Variable | Tipo | Descripción |
|---|---|---|
| carga_id | UUID | Identificador de la carga |
| nombre | String | Nombre de la carga |
| estado | Boolean | Encendida/apagada |
| brillo | Float (opcional) | Nivel de intensidad, solo aplica a luminarias regulables |
| potencia_nominal | Float | Potencia nominal |
| dispositivo_id | UUID | Dispositivo asociado |

El detalle de qué campos aplican según el tipo de carga (toma VTA+ o
luminaria) se define en la siguiente sección.

---

## 8. Variables específicas por tipo de dispositivo

### 8.1 Tomas inteligentes VTA+

Basado en el mapeo real capturado en `docs/Componentes/devices.json` y
`tuya-raw.json` (dispositivo de prueba "DOT IV", modelo VTA-84757):

| Variable Tuya | Tipo | Uso en el proyecto |
|---|---|---|
| `switch_1` | Boolean | Es el campo que se expone como `estado` en "Variables de cargas controlables" |
| `relay_status` | Enum (power_off/power_on/last) | Se fija en `last` por defecto: la toma recupera su último estado tras un corte de energía |
| `countdown_1` | Integer | Temporizador de apagado — no se usa en el alcance actual |
| `child_lock` | Boolean | Bloqueo infantil — no se usa en el alcance actual |
| `light_mode` | Enum | Modo del indicador LED de la toma — no se usa en el alcance actual |
| `cycle_time`, `random_time`, `switch_inching` | String | Programaciones avanzadas del dispositivo — no se usan en el alcance actual |

**Decisión:** del conjunto de variables reales que expone el dispositivo,
APP-GLAMPING solo consume `switch_1` (control on/off) y `relay_status`
(comportamiento tras corte de energía, fijado en `last`). El resto se deja
documentado por completitud, pero fuera de los requisitos funcionales
actuales (RF-10).

### 8.2 Luminarias inteligentes (TP-Link Tapo L530E)

| Variable | Tipo | Descripción |
|---|---|---|
| estado | Boolean | Encendida/apagada |
| brillo | Integer (0–100) | Porcentaje de intensidad lumínica |
| color | String (opcional) | Sin uso inmediato; el modelo lo soporta pero no está en el alcance de RF-09 |

**Nota de integración (no solo de variables):** el bombillo TP-Link Tapo
L530E pertenece al ecosistema TP-Link Tapo/Kasa, **no** al ecosistema Tuya
usado por las tomas VTA+. Su integración no puede reutilizar el protocolo
local `tinytuya` ya probado con las tomas; requiere su propia librería o
API (por ejemplo `python-kasa` o la API local/cloud de Tapo). La selección
definitiva de esa librería queda pendiente para el momento de la
implementación, ya que depende de la disponibilidad y estabilidad de las
opciones existentes en ese momento.

---

## 9. Variables de control (historial de acciones)

| Variable | Tipo | Descripción |
|---|---|---|
| comando_id | UUID | Identificador del comando |
| dispositivo_id | UUID | Dispositivo al que se dirige |
| accion | Enum | ON/OFF |
| usuario_id | UUID | Usuario que ejecutó la acción |
| fecha_hora | Timestamp | Momento de ejecución |
| resultado | Enum | Resultado del comando |

Esta tabla es la que da soporte al historial de acciones exigido en
`Requisitosapp.md` (RF-16): cada comando ejecutado sobre un dispositivo
queda asociado al usuario y al momento en que se realizó.

---

## 10. Variables de comunicación

| Variable | Tipo | Descripción |
|---|---|---|
| dispositivo_id | UUID | Identificador del dispositivo |
| topic_mqtt | String | Topic MQTT utilizado |
| conectado | Boolean | Estado de conexión |
| ultima_conexion | Timestamp | Última comunicación |

---

## 11. Variables de alertas

| Variable | Tipo | Descripción |
|---|---|---|
| alerta_id | UUID | Identificador de la alerta |
| domo_id | UUID | Domo al que corresponde la alerta |
| tipo_alerta | Enum | Tipo de condición detectada |
| variable_asociada | String | Variable que originó la alerta |
| valor_detectado | Float | Valor medido en el momento de la alerta |
| valor_limite | Float | Valor límite configurado |
| nivel_prioridad | Enum | Nivel de severidad de la alerta |
| estado | Enum | Estado de la alerta (activa/atendida/cerrada) |
| fecha_hora | Timestamp | Momento en que se generó la alerta |

**Decisión — catálogo de `tipo_alerta`:**

- `bateria_baja`
- `consumo_elevado`
- `generacion_nula`
- `dispositivo_desconectado`
- `sobrecarga`

**Decisión — catálogo de `nivel_prioridad`:**

- `informativo`
- `advertencia`
- `critico`

Los valores numéricos exactos que disparan cada alerta (por ejemplo, el
porcentaje de batería considerado "bajo") no se fijan en este documento
porque dependen del dimensionamiento energético definitivo del sistema
fotovoltaico, que sigue pendiente. Esos umbrales se guardan como datos en
"Variables de configuración del sistema" (sección 12), no como valores
fijos en el código.

**Decisión — nivel de la alerta `bateria_baja` que crea el control energético:**
`critico` si el SoC es menor o igual a la reserva de la batería del domo (sección
17.2); si no, `advertencia`. Solo se crea si se corta una toma `confort` o
`importante`.

---

## 12. Variables de configuración del sistema

| Variable | Tipo | Descripción |
|---|---|---|
| domo_id | UUID | Domo al que aplica la configuración |
| parametro | String | Nombre del parámetro configurable |
| valor | String | Valor asignado al parámetro |
| fecha_modificacion | Timestamp | Última modificación del parámetro |

Incluye, entre otros, los límites que disparan cada `tipo_alerta` y las
prioridades de carga usadas por el control energético.

La columna `configuracion_sistema.parametros_energeticos` guarda JSON como texto,
con una clave por funcionalidad. Un `PUT .../configuracion` reemplaza solo la
clave que trae y conserva las demás:

```json
{
  "control_energetico": {"umbral_soc_bajo": 30.0, "umbral_soc_recuperacion": 40.0},
  "bateria": {"tipo": "lifepo4", "capacidad_wh": 1200.0}
}
```

| Clave | Campo | Regla |
|---|---|---|
| `control_energetico` | `umbral_soc_bajo`, `umbral_soc_recuperacion` | No menos que la reserva de la batería y hasta 100; recuperación mayor que bajo. Los usan las tomas no esenciales sin umbrales propios. Sin valores, o con unos por debajo de la reserva, se usa el preset `confort` |
| `bateria` | `tipo` | `lifepo4` o `agm`. Decide reserva y presets (sección 17.2). Sin valor válido se usa `lifepo4` |
| `bateria` | `capacidad_wh` | Mayor que 0 |
| `perfil_control` | (el valor) | `conservador`, `equilibrado` o `maximo_uso`. Sin valor válido se usa `conservador` |

---

## 13. Mensajería MQTT

### 13.1 Convención de topics

**Telemetría — IMPLEMENTADO** (`backend/mqtt_listener.py`):

```
domo/{domo_id}/telemetria/{tipo}    → una lectura del tipo indicado
```

`{tipo}` es uno de `fotovoltaica` / `consumo` / `bateria` / `sensores`, y
mapea directamente a su tabla correspondiente (`lecturas_fotovoltaicas`,
`lecturas_consumo`, `lecturas_bateria`, `lecturas_sensores`).

**Por qué se ajustó respecto a la definición de trabajo original**
(`glamping/{domo_id}/telemetria/{dispositivo_id}`, un único topic
genérico por dispositivo con el campo `origen` distinguiendo el tipo de
medición dentro del payload):

- En el esquema real de la base de datos (`backend/models.py`),
  `lecturas_fotovoltaicas`, `lecturas_bateria` y `lecturas_sensores` son
  mediciones **a nivel de domo**: tienen `domo_id` pero NO tienen
  `dispositivo_id`. No existe un dispositivo individual detrás de "la
  generación fotovoltaica del domo" o "el estado de la batería del
  domo" — son variables del sistema completo, no de un dispositivo
  puntual. Un topic organizado por `{dispositivo_id}` no tiene con qué
  llenarse para estas tres tablas.
- Solo `lecturas_consumo` sí tiene `dispositivo_id` (opcional). Aun así
  se mantiene bajo el mismo esquema `domo/{domo_id}/telemetria/consumo`
  en vez de un topic propio por dispositivo, por consistencia con las
  otras tres: `dispositivo_id` viaja dentro del payload (ver 13.2), no en
  el path del topic.
- El prefijo cambió de `glamping/` a `domo/` — coincide con el nombre
  real del recurso (tabla `domos`) y con el vocabulario que ya usa el
  resto del backend (rutas REST `/domos/{domo_id}/...`).
- Se descartó el campo `origen` como discriminador: con un topic
  distinto por `{tipo}`, el tipo ya es explícito en el nombre del topic,
  así que no hace falta repetirlo dentro del payload.

**Comando de tomas — IMPLEMENTADO** (`backend/mqtt_publisher.py`):

```
domo/{domo_id}/comando/toma    → orden de conmutación hacia la toma física
```

Lo publica el backend (no el ESP32) cuando cambia el estado de una toma
con `id_fisico` no nulo: desde `PATCH .../dispositivos/{id}` (control
manual, R-11) o desde `POST .../control-energetico/evaluar` (algoritmo de
supervivencia, R-13). El ESP32 escucha ese topic y responde en
`domo/{domo_id}/estado/toma` (retenido, publicado por el firmware — ver
`firmware/domo_nodo/`); el backend todavía no consume esa respuesta.

Diferencias respecto al esquema genérico de más abajo, y por qué:

- El último segmento es el **tipo** de carga (`toma`), no el
  `dispositivo_id`: el firmware del nodo se suscribe a un topic fijo por
  domo, y el `dispositivo_id`/`id_fisico` viaja dentro del payload — el
  mismo criterio que ya se usó para la telemetría.
- Se publica con **`retain=False` siempre**, a diferencia de los
  mensajes de estado. Un comando retenido se lo volvería a entregar el
  broker al ESP32 en cada reconexión y el nodo reejecutaría una orden
  vieja (por ejemplo, apagaría la toma otra vez horas después de que
  alguien la encendió a mano). QoS 1: conmutar dos veces al mismo
  estado es inofensivo, perder la orden no.
- Los **bombillos no se controlan por esta vía**. Solo tipo='toma'.

**Resto del control y estado de dispositivos — PENDIENTE** (sin
implementar todavía; se definirán al construir el resto del control real
de R-10/R-11 y el estado de comunicación real de R-17):

```
glamping/{domo_id}/estado/{dispositivo_id}        → cambios de estado del dispositivo
glamping/{domo_id}/comando/{dispositivo_id}       → comandos enviados hacia el dispositivo
glamping/{domo_id}/confirmacion/{dispositivo_id}  → confirmación de ejecución del comando
glamping/{domo_id}/conexion/{dispositivo_id}      → conexión/desconexión (Last Will and Testament de MQTT)
```

Estos cuatro sí quedan organizados por `{dispositivo_id}` — a diferencia
de la telemetría, acá cada mensaje corresponde a un dispositivo real y
puntual de la tabla `dispositivos` (una bombilla, una toma), no a una
medición del domo completo. Se dejan tal como estaban en la definición de
trabajo original (prefijo `glamping/` incluido) porque todavía no se han
implementado ni decidido en firme; cuando se implementen deben usar
`domo_id`/`dispositivo_id` enteros (BIGINT), igual que la telemetría —
ver la corrección de tipos en 13.2.

### 13.2 Estructura de payloads (JSON)

**Telemetría — IMPLEMENTADO.** `domo_id` y `tipo` NO van dentro del
payload: se extraen del propio topic (`backend/mqtt_listener.py`,
`_procesar_mensaje`). `fecha_hora` es opcional en los cuatro — si se
omite, la base de datos le asigna la hora del servidor al insertar.

*`domo/{domo_id}/telemetria/fotovoltaica` → `lecturas_fotovoltaicas`:*

```json
{
  "voltaje": 24.6,
  "corriente": 1.8,
  "potencia_generada": 44.3,
  "energia_generada": 0.82,
  "estado_generacion": "generando",
  "fecha_hora": "2026-08-24T10:45:00Z"
}
```

*`domo/{domo_id}/telemetria/consumo` → `lecturas_consumo`:*

```json
{
  "dispositivo_id": 7,
  "voltaje": 120.0,
  "corriente": 2.0,
  "potencia_consumida": 240.0,
  "energia_consumida": 0.5,
  "fecha_hora": "2026-08-24T10:45:00Z"
}
```

`dispositivo_id` es opcional (entero, BIGINT — no UUID): una lectura de
consumo puede o no estar asociada a un dispositivo puntual. Si viene, debe
ser un dispositivo de ese mismo domo (sección 16).

Los cuatro payloads aceptan además `sensor_id` (entero, opcional). Los
valores inválidos se tratan como indica la sección 16.

*`domo/{domo_id}/telemetria/bateria` → `lecturas_bateria`:*

```json
{
  "voltaje": 12.9,
  "corriente": 1.5,
  "potencia": 19.4,
  "estado_carga_soc": 87.5,
  "temperatura": 28.4,
  "estado_bateria": "cargando",
  "fecha_hora": "2026-08-24T10:45:00Z"
}
```

**Convención de signo de `corriente` y `potencia` (batería):** positivas =
la batería se está **cargando**; negativas = se está **descargando**; 0 =
en reposo. Es la convención que usa `backend/tools/simulador_telemetria.py`
y la que debe seguir el ESP32.

**Valores de `estado_bateria`:** `cargando`, `descargando`, `cargada`
(SoC al 100 % con excedente de generación), `en_reposo` (sin flujo neto).

**Valores de `estado_generacion`** (telemetría fotovoltaica): `generando`,
`sin_generacion`.

*`domo/{domo_id}/telemetria/sensores` → `lecturas_sensores`:*

```json
{
  "tipo_sensor": "temperatura",
  "variable_medida": "temp_ambiente",
  "valor": 21.5,
  "unidad_medida": "C",
  "estado": "ok",
  "fecha_hora": "2026-08-24T10:45:00Z"
}
```

**Comando de toma — IMPLEMENTADO** (`backend/mqtt_publisher.py`,
`publicar_comando_toma`). `domo_id` NO va en el payload: se extrae del
topic, igual que en la telemetría.

*`domo/{domo_id}/comando/toma` → la toma física del domo:*

```json
{
  "accion": "off",
  "id_fisico": "toma-sala-01",
  "origen": "r13"
}
```

| Campo | Valores | Descripción |
|---|---|---|
| `accion` | `"on"` / `"off"` | Orden de conmutación. Cualquier otro valor es un error de programación del backend (ValueError), no se publica |
| `id_fisico` | String | `dispositivos.id_fisico` de esa toma (sección 6). Nunca NULL: las filas simuladas no llegan a publicarse |
| `origen` | `"r13"` / `"manual"` | Quién pidió el cambio: el algoritmo de supervivencia (R-13) o un usuario desde la API (R-11). Sirve para rastrear la orden desde el firmware o con `mosquitto_sub` |

Se publica siempre **después** del commit en la base de datos: el estado
guardado es la fuente de verdad del estado deseado, y el comando MQTT es
el efecto secundario que lo propaga al hardware. Si el broker está
caído, la publicación falla en silencio (queda en el log del backend) y
la respuesta HTTP del endpoint no cambia.

**Estado de dispositivo, confirmación y comando genérico — PENDIENTE.**
El comando de toma de arriba es lo único implementado; el payload
genérico por `dispositivo_id` de más abajo sigue siendo definición de
trabajo (no lo reemplaza para bombillos ni para otros tipos de carga).
Se dejan
como definición de trabajo original, con `dispositivo_id` corregido a
entero (BIGINT, coincide con `dispositivos.id` en el esquema real) en vez
de `uuid`. `comando_id` y `usuario_id` quedan como `uuid` sin resolver:
no existe todavía una tabla de comandos en el esquema, así que su tipo
definitivo se decide cuando se implemente R-10/R-11 (`usuario_id`, en
cambio, sí correspondería a un entero BIGINT como `usuarios.id` cuando
esto se construya, pero no se corrige aquí para no tocar un payload que
todavía no está implementado más de lo estrictamente necesario).

**Estado de dispositivo (ESP32 → broker, ante cualquier cambio):**

```json
{
  "dispositivo_id": 7,
  "estado": "encendido",
  "timestamp": "2026-08-24T10:45:00Z"
}
```

**Comando (backend → ESP32/dispositivo):**

```json
{
  "comando_id": "uuid",
  "dispositivo_id": 7,
  "accion": "ON",
  "usuario_id": "uuid",
  "timestamp": "2026-08-24T10:45:00Z"
}
```

**Confirmación de comando (ESP32 → backend):**

```json
{
  "comando_id": "uuid",
  "resultado": "ok",
  "timestamp": "2026-08-24T10:45:01Z"
}
```

La telemetría y el comando de toma (arriba) ya están implementados tal
como se documentan. Los tres payloads genéricos de control y estado de
dispositivo siguen siendo una definición de trabajo pendiente para
avanzar el desarrollo; pueden ajustarse durante la implementación si las
pruebas con el hardware real lo requieren. Nota: el comando de toma ya
implementado se desvió de esa definición (sin `comando_id`, sin
`usuario_id`, sin `timestamp`, `accion` en minúsculas y con `id_fisico`
en vez de `dispositivo_id`) porque se ajustó al contrato que el firmware
del ESP32 ya tenía probado — ver `firmware/domo_nodo/`.

---

## 14. Frecuencias de muestreo, publicación y almacenamiento

| Proceso | Frecuencia definida | Justificación |
|---|---|---|
| Muestreo interno en el ESP32 (lectura de sensores) | Cada 2 segundos | Suficiente para variables eléctricas de baja dinámica en un domo de baja potencia |
| Publicación MQTT de telemetría (tiempo real en la app) | Cada 5 segundos | Balance entre "tiempo real" percibido y tráfico de red/broker |
| Publicación de cambios de estado de dispositivos | Inmediata, por evento | El cambio de estado no debe esperar a un ciclo de muestreo |
| Almacenamiento en base de datos (Supabase/Postgres) | 1 registro por minuto | Postgres no es una base de series temporales; guardar cada 5 s las 24 h saturaría el almacenamiento sin necesidad real para este prototipo |

**Decisión:** para el almacenamiento histórico se guarda una lectura por
minuto (la última recibida en ese minuto). Los cambios de estado y los
comandos ejecutados sí se almacenan de forma inmediata y completa, porque
alimentan el historial de acciones (RF-16) y las alertas (RF-15), donde no
es aceptable perder eventos.

---

## 15. Pendientes reales (dependen de otras decisiones o de pruebas físicas)

- Umbrales numéricos exactos de cada `tipo_alerta` (por ejemplo, % de
  batería considerado bajo) — dependen del dimensionamiento energético
  definitivo del sistema fotovoltaico.
- Librería/API definitiva para integrar las luminarias TP-Link Tapo
  (distinta del protocolo Tuya usado en las tomas VTA+).
- Proveedor/hosting definitivo del broker MQTT (autoalojado vs. servicio
  administrado).
- Ajustes a la estructura de payloads MQTT y a las frecuencias definidas en
  las secciones 13 y 14, en caso de que las pruebas con el hardware real
  muestren la necesidad de cambiarlas.
- Qué hacer con un `sensor_id` inválido en una lectura de batería: hoy se
  descarta la lectura y R-13 no se evalúa con ella. Decidirlo cuando el
  firmware empiece a enviar `sensor_id` (ver el `TODO` en
  `backend/mqtt_listener.py`).

---

## 16. Integridad de datos (migración `b7d2c4e8a1f3`)

### 16.1 Catálogos y valores permitidos

Cada catálogo es una tabla con `codigo` (texto, clave primaria) y
`descripcion`. La columna de la tabla que lo usa sigue guardando el mismo
texto, con una llave foránea hacia `codigo`, así que leerla no requiere JOIN.

| Catálogo | Columna que lo usa | Valores |
|---|---|---|
| `rol_usuario` | `usuarios.rol` | `dueño`, `administrador` |
| `rol_actor` | `historial_acciones.rol` | `dueño`, `administrador`, `cliente`, `sistema` |
| `tipo_dispositivo` | `dispositivos.tipo` | `bombillo`, `toma`, `otro` |
| `tipo_accion` | `historial_acciones.accion_realizada` | `encender`, `apagar` |
| `tipo_alerta` | `alertas.tipo_alerta` | `bateria_baja`, `consumo_elevado`, `generacion_nula`, `dispositivo_desconectado`, `sobrecarga` |
| `nivel_prioridad` | `alertas.nivel_prioridad` | `informativo`, `advertencia`, `critico` |
| `estado_alerta` | `alertas.estado_alerta` | `activa`, `atendida`, `cerrada` |
| `estado_bateria` | `lecturas_bateria.estado_bateria` | `cargando`, `descargando`, `cargada`, `en_reposo` |
| `estado_generacion` | `lecturas_fotovoltaicas.estado_generacion` | `generando`, `sin_generacion` |
| `tipo_lectura` | `sensores.tipo_lectura` | `bateria`, `fotovoltaica`, `consumo`, `temperatura` |
| `categoria_dispositivo` | `dispositivos.categoria` | `esencial`, `importante`, `confort`, `prescindible` (migración `c4a8e2f7d1b6`, sección 17) |

**Decisión — `rol_actor` separado de `rol_usuario`:** `sistema` no es una
persona y el cliente nunca tiene cuenta, así que esos valores no deben ser
válidos en `usuarios.rol`.

Las columnas que aceptan NULL lo siguen aceptando. No tienen catálogo, entre
otras, `dispositivos.estado`, `lecturas_sensores.tipo_sensor` ni las columnas
de `estado_comunicacion`.

Para agregar un valor hace falta una migración que lo inserte en el catálogo.
En `estado_bateria` y `estado_generacion` hay que actualizar además los
`Literal` de `backend/schemas.py`, que también usa el listener.

### 16.2 Sensores

Tabla `sensores`: un sensor físico al que se puede atribuir una lectura.
Ningún endpoint los gestiona todavía.

| Columna | Tipo | Descripción |
|---|---|---|
| id | BIGSERIAL | Identificador del sensor |
| domo_id | BIGINT | Domo al que pertenece (obligatorio; se borra con el domo) |
| tipo_lectura | Text | Qué mide, según el catálogo `tipo_lectura` (obligatorio) |
| nombre | Text | Nombre del sensor (obligatorio) |
| unidad_medida | Text (opcional) | Unidad de medida |
| id_fisico | Text (opcional) | Identificador del sensor físico |

`sensor_id` es una columna opcional (BIGINT, llave foránea hacia `sensores`) en
`lecturas_bateria`, `lecturas_fotovoltaicas`, `lecturas_consumo` y
`lecturas_sensores`. Al borrar un sensor queda en NULL y la lectura se conserva.
La API REST lo devuelve pero no lo recibe; por MQTT sí se puede enviar. La base
de datos solo exige que el sensor exista, no que sea del mismo domo que la lectura.

### 16.3 Restricciones

| Restricción | Regla |
|---|---|
| `lecturas_bateria_estado_carga_soc_check` | `estado_carga_soc` entre 0 y 100 (rechaza NaN e Infinity; NULL permitido) |
| `dispositivos_umbrales_soc_check` | `umbral_reactivacion_soc >= umbral_apagado_soc + 5`, cuando ambos tienen valor (migración `c4a8e2f7d1b6`, sección 17.3) |
| `dispositivos_domo_id_id_fisico_key` | `id_fisico` único dentro del domo; se permiten varios NULL (dispositivos simulados) |
| `dispositivos_id_domo_id_key` | `UNIQUE(id, domo_id)`: destino de las llaves foráneas compuestas |
| `historial_acciones_dispositivo_domo_fkey`, `lecturas_consumo_dispositivo_domo_fkey` | `(dispositivo_id, domo_id)` debe ser un dispositivo de ese domo. Con `ON DELETE SET NULL (dispositivo_id)`: al borrar el dispositivo, la fila se conserva con su `domo_id` |

`estado_comunicacion` no cambia: sigue siendo un historial 1:N por dispositivo.

### 16.4 Valores inválidos en la telemetría

| Dato recibido | API REST | Listener MQTT |
|---|---|---|
| Estado fuera de catálogo (`estado_bateria`, `estado_generacion`) | 422 | Guarda NULL en ese campo, conserva la lectura y deja un WARNING |
| SoC entre 0 y 100 | Se guarda | Se guarda |
| SoC entre -5 y 105, fuera de 0–100 | 422 | Lo limita a 0–100 y deja un WARNING |
| SoC fuera de -5 a 105, o que no es un número finito | 422 | Guarda NULL y deja un WARNING |
| `dispositivo_id` inexistente o de otro domo | 422 | Descarta la lectura y deja un error en el log |
| `sensor_id` inexistente | No aplica: la API no lo recibe | Descarta la lectura y deja un error en el log |
| Otro valor numérico fuera de los límites de abajo, o que no es un número | 422 | Guarda NULL en ese campo, conserva la lectura y deja un WARNING |

Límites de los valores numéricos (`LIMITES_TELEMETRIA` en `backend/schemas.py`).
Protegen las columnas `real`, que no admiten valores como `1e300` ni `1e-300`:

| Campo | Mínimo | Máximo |
|---|---|---|
| `voltaje` (V) | -10 | 1000 |
| `corriente` (A) | -1000 | 1000 |
| `potencia` (W) | -100 000 | 100 000 |
| `potencia_generada`, `potencia_consumida` (W) | -100 | 100 000 |
| `energia_generada`, `energia_consumida` (Wh) | 0 | 1 000 000 000 |
| `temperatura` (°C) | -50 | 150 |
| `valor` de un sensor, `valor_detectado` y `valor_limite` de una alerta | -1 000 000 000 | 1 000 000 000 |

Un valor con magnitud menor que `1e-30` se guarda como 0.

El listener conserva la lectura para no dejar a R-13 sin datos. Limita solo los
desvíos pequeños porque un dato absurdo, limitado a 0 o a 100, haría que R-13
cortara o reactivara tomas sin motivo. Una lectura con SoC NULL no mueve ninguna
toma: R-13 no se puede evaluar con ella.

En la API REST, un NaN, Infinity o número desbordado como `1e999` responde 422 en
cualquier cuerpo JSON, y como texto (`"NaN"`) en cualquier campo numérico. Un `null`
explícito en un campo que no puede quedar vacío también responde 422.

---

## 18. Sesiones y atención de alertas (migración `d5b1f3a9c2e7`)

| Columna | Tipo | Descripción |
|---|---|---|
| `usuarios.version_sesion` | Integer, obligatoria, por defecto 0 | Viaja en el JWT. Al cambiar o restablecer la contraseña sube, y los tokens anteriores dejan de valer |
| `alertas.fecha_atendida` | Timestamp, opcional | Cuándo pasó a `atendida`. Reabrir la alerta la borra y cerrarla la conserva. NULL en las atendidas antes de esta migración |

Migración `e6c2a8d4f1b9`:

| Columna | Tipo | Descripción |
|---|---|---|
| `dispositivos.desactivado_en` | Timestamp, opcional | Cuándo se desactivó; NULL si está activo. Termina el tiempo de un corte de R-13 en los reportes. En los ya inactivos al migrar, la fecha de su última acción, o la de la migración si no tenían ninguna |
| `usuarios.debe_cambiar_contrasena` | Boolean, obligatoria, por defecto false | Contraseña temporal: true al crear o restablecer la cuenta desde Administradores, false al cambiarla en Mi cuenta. Mientras es true, la API solo permite ver el propio perfil y cambiar la contraseña |

Migración `f7d3b9e5a2c8`:

| Columna | Tipo | Descripción |
|---|---|---|
| `domos.es_demostracion` | Boolean, obligatoria, por defecto false | Domo de demostración con datos sintéticos (`tools/generar_datos_demo.py`). Ningún endpoint lo cambia. La app y las exportaciones lo marcan como "Demostración · datos sintéticos" |

---

## 17. Categorías de dispositivo (migración `c4a8e2f7d1b6`)

### 17.1 Catálogo y columnas

El catálogo `categoria_dispositivo` indica qué tan protegida está una carga frente
al control energético (R-13):

| Categoría | Significado |
|---|---|
| `esencial` | R-13 nunca la corta; el apagado manual sigue permitido. Los bombillos siempre lo son |
| `importante` | Se corta al llegar a la reserva de la batería |
| `confort` | Se corta 15 puntos por encima de la reserva. Es la de una toma nueva si no se indica otra |
| `prescindible` | Se corta 30 puntos por encima de la reserva |

Columnas agregadas a `dispositivos`:

| Columna | Tipo | Descripción |
|---|---|---|
| categoria | Text, obligatoria | Llave foránea hacia `categoria_dispositivo` |
| ubicacion | VARCHAR(100), opcional | Dónde está dentro del domo |
| activo | Boolean, obligatoria, por defecto `true` | `false`: R-13 lo ignora y los listados lo ocultan salvo con `?incluir_inactivos=true`. No hay borrado físico, y desactivar no apaga la toma |

La API devuelve además `personalizado`, que no es una columna: es `true` si los
umbrales propios no coinciden con el preset de la categoría para la batería del
domo. Una toma sin umbrales propios no lo es: usa los globales del domo, que por
defecto son el preset `confort`.

### 17.2 Presets

Dependen de la reserva de la batería del domo (`lifepo4` = 20, `agm` = 50) y de su
perfil de control. El apagado es:

| Categoría | `conservador` (por defecto) | `equilibrado` | `maximo_uso` | Prioridad |
|---|---|---|---|---|
| `esencial` | R-13 nunca la corta | | | 40 |
| `importante` | reserva | reserva | reserva | 30 |
| `confort` | reserva + 15 | reserva + 10 | reserva + 5 | 20 |
| `prescindible` | reserva + 30 | reserva + 20 | reserva + 10 | 10 |

La reactivación es siempre el apagado + 10. Con `agm` y `conservador`, por ejemplo:
`importante` 50 / 60, `confort` 65 / 75, `prescindible` 80 / 90.

Prioridad más baja = se corta primero; a igual prioridad, por id. `GET
/domos/{id}/categorias` devuelve estos valores calculados para ese domo, también
los de los tres perfiles para la vista previa.

- Elegir una categoría copia su preset a los umbrales y a la prioridad. Reenviar la
  misma lo vuelve a aplicar.
- Al cambiar el tipo de batería o el perfil, solo pasan al preset nuevo las tomas que
  coincidían con el anterior. Las personalizadas no se tocan.
- En modo avanzado, el umbral de apagado no puede quedar por debajo de la reserva
  (422). Una toma personalizada que quede así por cambiar de batería se puede seguir
  operando, pero para editar sus umbrales hay que subirlos.
- La migración asignó a cada toma la categoría de preset `lifepo4` más cercana sin
  cambiar sus umbrales, y `confort` a las que no tenían umbrales. Por eso una toma
  migrada puede figurar como personalizada.

### 17.3 Restricciones

| Restricción | Regla |
|---|---|
| `dispositivos_umbral_apagado_rango_check` | `umbral_apagado_soc` entre 0 y 100 |
| `dispositivos_umbral_reactivacion_rango_check` | `umbral_reactivacion_soc` entre 0 y 100 |
| `dispositivos_umbrales_par_check` | Ambos umbrales o ninguno |
| `dispositivos_umbrales_soc_check` | Reactivación >= apagado + 5. Compara en `numeric` porque las columnas son `real`: comparadas así, bandas exactas como 0,1 / 5,1 fallarían por precisión |

Nota: NaN e Infinity no son JSON estándar. Con ellos, FastAPI no logra serializar
su propio mensaje de error y responde 500, pero la lectura no se guarda.

### 17.4 Primacía de la decisión humana (ronda DB-4, modelo v2)

Si la última acción sobre una toma es un encendido hecho por una persona (dueño o
administrador), R-13 no la corta por el umbral de apagado de su categoría hasta que,
después de esa acción, el SoC llegue a su umbral de reactivación; desde ahí vuelve a la
regla normal. Excepción: con el SoC bajo la reserva de la batería del domo la corta
igual, y la acción queda con `motivo=reserva` en la condición. No usa columnas nuevas:
sale de `acciones` y `mediciones`. Detalle en `docs/modelo_v2/MAPEO.md`, sección 9.
