# Backend — Vatia

## Modelo v2 (rama `feature/modelo-simplificado`)

Desde la ronda DB-2 el backend, el listener y las herramientas usan el **modelo v2**: otro
proyecto de Supabase, con 7 tablas (`usuarios`, `domos`, `asignaciones`, `dispositivos`,
`acciones`, `alertas`, `mediciones`) y el esquema `bi` para Power BI. El detalle, el mapeo
de cada columna de v1 y los cambios de contrato están en `docs/modelo_v2/MAPEO.md`; el
procedimiento del cambio y de la vuelta a v1, en `docs/modelo_v2/CAMBIO_DEFINITIVO.md`.

- **Conexión:** `database.py` usa solo `DATABASE_URL_V2` (en `.env`; ver `env.example`).
  `DATABASE_URL` (la base v1) solo la leen `tools/respaldo_bd.py --origen v1` y
  `alembic_v2/env.py`, que se niega si las dos apuntan a la misma base.
- **Migraciones:** `alembic -c alembic_v2.ini upgrade head` (revisiones `2b8e4f1a6c90`
  tablas, `7c3d9a1e4b52` voltaje FV y temperatura, `4e8b2c6d9f17` esquema `bi` y rol
  `lector_bi`). La carpeta `alembic/` es la línea v1 y no se usa en esta rama.
- **Telemetría:** todo va a `mediciones`, una fila por domo y bloque de 10 s
  (`servicios/mediciones.py`). Los endpoints `/lecturas-*` siguen respondiendo con la
  forma de v1.
- **Datos actuales:** el Panel (`/estado-general`) y R-13 solo usan una medida si tiene
  como mucho 2 minutos (`servicios/mediciones.VIGENCIA_S`). Más vieja, el Panel la da
  como `null` y R-13 no corta ni reactiva (`/evaluar` responde 409 con el motivo; el
  listener lo deja una sola vez en el log).
- **Copia de v1 a v2:** `tools/respaldo_bd.py --origen v1` y después
  `tools/copiar_a_v2.py` (ensayo por defecto; `--aplicar --aceptar …`). El ensayo muestra
  la energía total por domo en el respaldo y en v2.
- **Keepalive:** `keepalive.py` hace ping a `DATABASE_URL` y a `DATABASE_URL_V2` (secreto
  de GitHub con ese nombre).

Las secciones de abajo sobre `alembic/` y las tablas `lecturas_*` describen la línea v1
(etiqueta `v1-modelo-actual`).

## Domo de demostración (datos sintéticos)

Para ver Reportes y Power BI con un mes de datos sin hardware. Requiere la migración
`f7d3b9e5a2c8`. Desde `backend/`, con el entorno virtual activo, primero sin escribir nada:

```
python tools/generar_datos_demo.py --dueno-correo <correo del dueño> --solo-calcular
python tools/generar_datos_demo.py --dueno-correo <correo del dueño>
```

Crea, si no existe, el domo "Domo demostración" del dueño, marcado con `es_demostracion`, con una
batería AGM de 1200 Wh, perfil equilibrado y cuatro dispositivos sin `id_fisico` (Bombillo,
Nevera, Cargadores, Luces decorativas). Genera 30 días de lecturas cada 5 minutos que terminan
ahora, aplica R-13 con las reglas del backend y agrega algunas acciones manuales. Muestra un
resumen y pide confirmación con "s"; todo se escribe en una transacción.

| Parámetro | Por defecto | Uso |
|---|---|---|
| `--dias` | 30 | Días hacia atrás desde ahora |
| `--wp` | 500 | Potencia pico de los paneles, como el simulador |
| `--semilla` | 2026 | Clima y uso; la misma semilla da los mismos datos para los mismos días |
| `--tipo-bateria`, `--capacidad-wh`, `--perfil` | agm, 1200, equilibrado | La batería y el perfil del domo |
| `--sin-acciones-manuales` | | Solo acciones del sistema |
| `--reiniciar` | | Borra solo los datos del domo de demostración y los genera de nuevo |
| `--domo-id` | | Si el dueño tuviera más de un domo de demostración |

Se detiene sin escribir si el domo con ese nombre no es de demostración, si `--domo-id` no
cuadra o si ya hay datos y no se pidió `--reiniciar`. No imprime la conexión.

## Si el dueño olvidó su contraseña

Nadie puede restablecérsela desde la app. En el PC que tiene `backend/.env`, desde `backend/`
con el entorno virtual activo:

```
python tools/restablecer_contrasena.py
```

Pide el correo y muestra el nombre y el rol para confirmar que es la cuenta correcta. Luego pide
la contraseña nueva dos veces, sin mostrarla, con la misma validación que la app (de 8
caracteres a 72 bytes). Cierra todas las sesiones abiertas de esa cuenta, y la contraseña queda
definitiva: no se pide cambiarla al entrar. No imprime la conexión, hashes ni la contraseña.
Sirve para cualquier cuenta, pero para un administrador lo normal es que el dueño la restablezca
desde la página Administradores.

API en FastAPI del sistema de gestión y monitoreo energético para el domo
experimental.

## Estructura actual

- `database.py` — conexión SQLAlchemy a Supabase/Postgres (ya existente).
- `models.py` — modelos ORM que reflejan el esquema ya creado en Supabase
  (ya existente).
- `main.py` — aplicación FastAPI. Por ahora expone únicamente `/` y
  `/health`; los demás endpoints se agregan sobre esta base.
- `test_connection.py` — script de prueba directa de conexión a la base
  de datos (ya existente, independiente de FastAPI).
- `requirements.txt` — dependencias del backend.
- `alembic/`, `alembic.ini` — migraciones de base de datos (ver sección
  "Migraciones" más abajo).
- `mqtt_listener.py` — cliente MQTT del backend, corre aparte de uvicorn
  (ver sección "MQTT" más abajo).
- `mqtt_publisher.py` — publicador MQTT: manda las órdenes de
  encendido/apagado hacia la toma física real. A diferencia del
  listener, NO es un proceso aparte: lo importan los routers y vive
  dentro de uvicorn (ver sección "MQTT" más abajo).

## Configuración

1. Copia `env.example` a `.env` y completa la contraseña de la base de
   datos (Supabase Dashboard → Project Settings → Database → Database
   password). **Nunca subas `.env` a GitHub.**
2. Crea un entorno virtual e instala las dependencias:

   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   pip install -r requirements.txt
   ```
3. (Opcional) `CORS_ORIGINS`: orígenes del frontend web autorizados a
   llamar al API, separados por comas (p. ej.
   `CORS_ORIGINS=http://localhost:5173,https://mi-dominio.com`). Si no se
   define, se permite solo `http://localhost:5173` (servidor de desarrollo
   de Vite en `frontend/`). El token viaja en el header `Authorization`,
   no en cookies, así que CORS se configura sin credenciales.

## Ejecutar

```
uvicorn main:app --reload
```

o directamente:

```
python main.py
```

El servicio queda disponible en `http://127.0.0.1:8000`, con documentación
interactiva automática en `http://127.0.0.1:8000/docs`.

## Probar que todo corre

```
curl http://127.0.0.1:8000/health
```

- Si la API y la conexión a la base de datos funcionan, responde
  `{"status": "ok", "api": "up", "database": "connected"}`.
- Si la base de datos no responde, responde HTTP 503 con el detalle del
  error, sin derribar el servicio.

## Migraciones (Alembic)

`alembic/env.py` lee `DATABASE_URL` de `.env` (mismo patrón que
`database.py`) y usa `Base.metadata` (importado desde `models.py`) como
`target_metadata`, así que `alembic revision --autogenerate` compara la
base de datos real contra el esquema definido en los modelos.

**Estado actual del proyecto:** la base de datos de Supabase ya tenía las
12 tablas creadas a mano antes de introducir Alembic aquí. La primera
migración (`alembic/versions/..._esquema_inicial_completo.py`) captura
ese esquema completo, pero se aplicó con `alembic stamp head` — NO con
`alembic upgrade head` — para marcarla como "ya aplicada" sin volver a
crear las tablas ni tocar ningún dato. No corras `alembic upgrade head`
contra esa misma base de datos: fallaría porque las tablas ya existen.

**Para levantar el proyecto desde cero** (una base de datos nueva y
vacía, por ejemplo en otro entorno):

```
alembic upgrade head
```

Esto sí ejecuta la migración inicial completa y crea las 12 tablas.

**Para crear una migración nueva** después de modificar `models.py` (y
aplicar el cambio real en Supabase, como se ha venido haciendo hasta
ahora — ver docs/arquitectura_sistema.md):

```
alembic revision --autogenerate -m "descripcion_del_cambio"
alembic stamp head
```

(`stamp`, no `upgrade`, mientras el equipo siga aplicando los cambios de
esquema directamente en Supabase antes de generar la migración — así
Alembic queda documentando el historial sin intentar re-ejecutar un DDL
que ya se aplicó por otro lado.)

**Excepción — `e1857d5e5e9d_agrega_id_fisico_a_dispositivos.py`:** esta
migración sí se escribió a mano (sin `--autogenerate`) y sí se aplica
con `alembic upgrade head`, porque el cambio NO se hizo antes en
Supabase — Alembic es el que crea la columna `dispositivos.id_fisico`:

```
alembic upgrade head
```

Es un único `add_column` de una columna nullable y sin `server_default`:
no reescribe ninguna fila existente (todas quedan en NULL, es decir,
siguen siendo dispositivos simulados) y `downgrade` la elimina.

## MQTT

`mqtt_listener.py` es un proceso aparte de uvicorn — recibe telemetría
real por MQTT y la guarda directamente en `mediciones` (modelo v2; en v1, en las tablas
`lecturas_*`), como
entrada ADICIONAL a los endpoints POST de R-6/R-7/R-8/R-9 (que siguen
funcionando igual para pruebas manuales desde `/docs`).

1. Mosquitto corriendo (en el equipo de desarrollo es un servicio
   nativo de Windows: `Get-Service mosquitto`; alternativa, desde la raíz
   del repo: `docker compose up -d`).

2. En una terminal aparte (con el `.venv` de `backend/` activado):

   ```
   python mqtt_listener.py
   ```

3. Publica un mensaje de prueba (ver el detalle de los 4 temas y sus
   campos en el docstring de `mqtt_listener.py`), por ejemplo:

   ```
   mosquitto_pub -h localhost -p 1883 -t "domo/1/telemetria/fotovoltaica" -m "{\"voltaje\": 24.6, \"potencia_generada\": 44.3, \"energia_generada\": 0.82}"
   ```

   Reemplaza `1` por un `domo_id` que exista de verdad en tu base de
   datos — si no existe, el listener descarta el mensaje y lo loguea como
   error (no crea el domo).

### Cómo guarda el listener (cola + hilo trabajador)

El callback de MQTT solo encola cada mensaje; un único hilo trabajador lo
guarda en la base de datos y ejecuta R-13, en el mismo orden de llegada.
Cada lectura cuesta tres viajes a Supabase (BEGIN + INSERT + COMMIT): la
conexión del listener se reutiliza (sin `pool_pre_ping`, con un reintento
si se cayó), la existencia del domo se verifica una sola vez y no hay
`refresh()` tras el commit. Por cada mensaje, el log indica cuánto tardó,
cuánto esperó en la cola y cuántos quedan en espera:

```
Insertado en lecturas_consumo: id=2947 domo_id=1 campos=[...] — 0.45 s
Procesado 'domo/1/telemetria/consumo' en 0.45 s (esperó 0.12 s en cola; en espera: 0).
```

Si "en espera" crece sin parar, la base de datos no da abasto con la
frecuencia de la telemetría. Con `Ctrl+C`, el listener termina de guardar
lo que ya estaba en cola (hasta 30 s) antes de salir.

### Evaluación automática de R-13 (`R13_AUTO`)

Después de insertar con éxito una lectura de **batería**, `mqtt_listener.py`
ejecuta el algoritmo de supervivencia (R-13) para ese domo — la misma
función que usa `POST /domos/{domo_id}/control-energetico/evaluar`
(`servicios/control_energetico.py`), con las mismas reglas, el mismo
historial (`rol='sistema'`) y la misma publicación MQTT posterior al
commit. Así R-13 actúa solo, sin que nadie llame al endpoint.

| Variable | Por defecto | Descripción |
|---|---|---|
| `R13_AUTO` | `true` | `false` desactiva la evaluación automática (el endpoint `/evaluar` sigue funcionando igual) |
| `R13_AUTO_INTERVALO_S` | `30` | Como máximo una evaluación por domo cada este número de segundos; las lecturas que llegan antes solo se insertan |

Si la evaluación falla, se registra en el log y el listener sigue funcionando; la
lectura ya quedó guardada. Al arrancar, el listener indica en el log si está activada.

**Sin SoC actual, R-13 no decide** (ronda DB-3). Si el último SoC con valor tiene más de
2 minutos (`servicios/mediciones.VIGENCIA_S`), o la lectura de batería más reciente
llegó sin SoC (un ESP32 sin sensor lo publica en `null`), no se corta ni se reactiva
nada. El listener lo deja en el log una vez, con el motivo, y no lo repite cada 30 s:

```
[WARNING] R-13 automático (domo 1) no decide: No hay estado de carga (SoC) de los últimos 2 minutos: el último registrado es 51.1 % del 2026-11-05 14:03 (hora de Colombia). … (no se repite mientras siga igual)
```

Cuando vuelve a llegar un SoC actual, lo dice ("vuelve a haber SoC actual") y evalúa
como siempre.

**Alertas automáticas (R-15).** Cada evaluación de R-13 (automática o por
`/evaluar`) que corta al menos una toma `confort` o `importante` crea una
alerta `bateria_baja` (`critico` si SoC ≤ reserva de la batería del domo:
lifepo4 20 %, agm 50 %; si no, `advertencia`; valor
detectado = SoC, límite = umbral de la primera de esas tomas; descripción
"R-13 cortó cargas: …"), salvo que el domo ya tenga una `bateria_baja`
activa. Cortar solo tomas `prescindible` no alerta: es el modo `ahorro`.
Cuando una evaluación reactiva tomas y ya no queda ninguna `confort` o
`importante` apagada por el sistema, esa alerta de R-13 pasa a `atendida`.
La alerta se guarda en la misma
transacción que las acciones (antes del commit y de la publicación MQTT).
Detalle en `servicios/control_energetico.py`.

### Comandos hacia la toma física (`mqtt_publisher.py`)

Es el camino de SALIDA, y corre dentro de uvicorn — no hay que arrancar
nada aparte. Cuando se cambia el estado de una toma con
`dispositivos.id_fisico` no nulo, el backend publica en
`domo/{domo_id}/comando/toma` un JSON
`{"accion": "on"|"off", "id_fisico": "...", "origen": "r13"|"manual"}`
(qos=1, `retain=False` siempre), y el ESP32 conmuta la toma Tuya real.

Las dos vías que publican:

- `PATCH /domos/{domo_id}/dispositivos/{id}` con `estado` —
  `origen="manual"` (R-11).
- `POST /domos/{domo_id}/control-energetico/evaluar` — `origen="r13"`
  (algoritmo de supervivencia).

Solo tipo='toma', y solo con `id_fisico` no nulo: las filas con
`id_fisico` NULL son dispositivos simulados y no publican nada. Los
bombillos nunca se controlan por esta vía.

Para ver los comandos que salen, sin necesidad del ESP32:

```
mosquitto_sub -h localhost -p 1883 -t "domo/+/comando/toma" -v
```

Si Mosquitto está caído, la publicación falla en silencio (queda en el
log de uvicorn) y el endpoint responde exactamente igual: el estado ya
se guardó en la base de datos, que es la fuente de verdad.

### Simulador de telemetría (`tools/simulador_telemetria.py`)

Publica lecturas realistas de batería, generación fotovoltaica y consumo
en `domo/{domo_id}/telemetria/{bateria|fotovoltaica|consumo}` (los mismos
temas y campos que espera `mqtt_listener.py`), para probar el panel del
frontend y el algoritmo de supervivencia (R-13) sin hardware. **Nunca**
publica en temas de comando (`domo/+/comando/#`).

Requiere Mosquitto corriendo (en el equipo de desarrollo es un servicio
nativo de Windows: `Get-Service mosquitto`; alternativa:
`docker compose up -d`) y `mqtt_listener.py` corriendo, que es quien
inserta las lecturas en la base de datos. Usa
`MQTT_BROKER_HOST`/`MQTT_BROKER_PORT` de `.env`. Desde `backend/`, con el
`.venv` activado:

```
python tools/simulador_telemetria.py --domo 2                          # escenario ciclo, cada 5 s
python tools/simulador_telemetria.py --domo 2 --intervalo 3
python tools/simulador_telemetria.py --domo 2 --soc-inicial 25         # batería baja para probar R-13
python tools/simulador_telemetria.py --domo 2 --escenario demo-r13     # demostración repetible de R-13
```

**Nunca sobre un domo cuyo ESP32 publica telemetría real** (hoy, el domo 1): las dos fuentes
se mezclarían en las mismas tablas y R-13 movería las tomas reales con un SoC inventado. Por eso:

- `--domo` es obligatorio: no hay domo por defecto.
- Antes de publicar, el simulador mira en la base, solo leyendo, la última lectura de batería,
  fotovoltaica y consumo del domo. Si hay alguna de los últimos 10 minutos, otra fuente lo está
  alimentando y se niega a correr. Si fue un simulador que acabas de detener, espera 10 minutos.
- También se niega si no puede comprobarlo: con `--sin-bd` o si la base no responde.
- `--forzar` salta la comprobación y solo avisa. Úsalo sabiendo lo que haces.

| Opción | Por defecto | Descripción |
|---|---|---|
| `--escenario` | `ciclo` | `ciclo` (día/noche aleatorio) o `demo-r13` (perfil determinista, ver abajo) |
| `--domo` | obligatorio | `domo_id` destino (debe existir en la base de datos). Nunca el de un ESP32 real |
| `--forzar` | | Publica aunque el domo haya recibido telemetría de otra fuente en los últimos 10 min, o aunque no se pueda comprobar |
| `--intervalo` | `5` | Segundos entre publicaciones |
| `--soc-inicial` | el SoC máximo (`70` en ciclo sin umbrales conocidos) | SoC inicial de la batería, entre 5 y 100 % |
| `--ciclo` | `900` | *ciclo:* segundos reales que dura un día simulado (mitad día, mitad noche; arranca al amanecer) |
| `--soc-bajo`, `--soc-alto` | según los umbrales del domo | SoC mínimo y máximo de los dos escenarios |
| `--sin-bd` | — | No lee la base de datos: ni los umbrales ni la comprobación de otra fuente. Por eso exige `--forzar` |
| `--capacidad-ah` | `100` | *ciclo:* capacidad del banco de 12 V, en Ah (batería real: CSB HRL12390W, 12 V 100 Ah) |
| `--t-descarga` | `180` | *demo-r13:* segundos de la fase DESCARGA |
| `--t-bajo` | `120` | *demo-r13:* segundos de la fase BAJO |
| `--t-recuperacion` | `180` | *demo-r13:* segundos de la fase RECUPERACIÓN |

**Escenario `ciclo`:** sistema de 12 V y ~500 Wp; generación con curva
solar acelerada y nubosidad aleatoria; consumo entre 30 y 120 W; el SoC
sube o baja según el balance (limitado a 5–100 %) con el tiempo
acelerado, para ver un día completo en pocos minutos.
`energia_generada`/`energia_consumida` son, en cambio, la energía del
intervalo **real** entre publicaciones, en Wh: potencia × `--intervalo` /
3600. Así los totales de R-14 (`GET /domos/{id}/historico`) son
coherentes con las potencias (p. ej. 65 W de media durante 1 h ≈ 65 Wh).
Cada línea de consola indica `[DÍA]` o `[NOCHE]`.

**Escenario `demo-r13`:** perfil determinista y repetible (sin ningún
valor aleatorio) para demostrar R-13. El SoC depende solo del tiempo
transcurrido:

| Fase | Duración | Qué pasa |
|---|---|---|
| `DESCARGA` | `--t-descarga` (180 s) | Sin generación, consumo constante de 80 W; el SoC baja linealmente de `--soc-inicial` a 10 % |
| `BAJO` | `--t-bajo` (120 s) | SoC fijo en 10 %; generación débil (40 W) igual al consumo reducido (balance 0). Tiempo para que R-13 actúe y para tomar capturas |
| `RECUPERACIÓN` | `--t-recuperacion` (180 s) | Generación alta (380 W); el SoC sube linealmente de 10 % a 90 % |
| `FIN` | — | SoC fijo en 90 % (balance 0); la consola avisa "Escenario terminado" y sigue publicando hasta `Ctrl+C` |

Ambos escenarios leen al arrancar los umbrales de las tomas activas no esenciales
del domo y fijan con ellos el SoC mínimo (el apagado más bajo menos 10) y el máximo
(la reactivación más alta más 10). Con toma 2 en 65/75 y toma 3 en 50/60, el SoC va
de 85 % a 40 %: R-13 corta en la `DESCARGA` y reactiva en la `RECUPERACIÓN`. Si esas
tomas tienen `id_fisico`, se mueve el hardware real. `--soc-bajo` y `--soc-alto` los
fijan a mano; `--sin-bd` no consulta la base (y entonces hace falta `--forzar`).
Cada línea de consola indica la fase: `[DESCARGA]`, `[BAJO]`,
`[RECUPERACIÓN]` o `[FIN]`.

En ambos escenarios el voltaje de batería va de ≈11,8 V a 13,0 V y la
corriente/potencia de batería son positivas al cargar y negativas al
descargar (ver `docs/Requisitos/Variables.md`). `Ctrl+C` lo detiene.

El simulador solo envía telemetría: R-13 actúa sobre las tomas porque
`mqtt_listener.py` lo evalúa automáticamente con cada lectura de batería
(ver "Evaluación automática de R-13"), o manualmente con
`POST /domos/{domo_id}/control-energetico/evaluar` (botón "Evaluar R-13
ahora" del panel).
