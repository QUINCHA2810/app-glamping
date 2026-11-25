# Vatia — arquitectura y reglas del sistema

Reglas de funcionamiento del backend, del control energético (R-13) y del contrato MQTT con el
ESP32. Complementa `backend/README.md`, `firmware/README.md`, `docs/modelo_v2/MAPEO.md` y
`docs/Requisitos/Variables.md`.

## Modelo de datos v2

La app usa el **modelo v2**, en otro proyecto de Supabase (Oregón, us-west-2;
conexión por el pooler de SESIÓN, puerto 5432 — el 6543 resultó unas 3 veces más lento):

- `backend/database.py` lee `DATABASE_URL_V2`; migraciones con `alembic -c alembic_v2.ini`
  (`2b8e4f1a6c90` tablas, `7c3d9a1e4b52` voltaje FV y temperatura, `4e8b2c6d9f17` esquema `bi`
  y `lector_bi`). 7 tablas en árbol: `usuarios`, `domos`, `asignaciones`, `dispositivos`,
  `acciones`, `alertas`, `mediciones`. Sin catálogos (son CHECK). RLS activo y sin políticas.
- El rol es POR DOMO (`asignaciones.rol`: `dueno` o `administrador`); la API sigue diciendo
  `dueño`. Un domo tiene un solo dueño.
- Telemetría: `mediciones`, una fila por domo y bloque de 10 s (`servicios/mediciones.py`).
- Datos actuales: el Panel y R-13 solo usan medidas de los últimos 2 minutos
  (`servicios/mediciones.VIGENCIA_S`); más viejas, el Panel muestra "sin dato" y R-13 no decide.
- Todo el detalle (mapeo de columnas, cambios de contrato, decisiones): `docs/modelo_v2/MAPEO.md`.
  Cambio de v1 a v2 y vuelta atrás: `docs/modelo_v2/CAMBIO_DEFINITIVO.md`.

Lo que sigue en esta sección describe la base **v1** (etiqueta `v1-modelo-actual`), que se
conserva para poder volver.

## Estado de la base de datos v1 (Supabase)

- Proyecto: `[REF-DEL-PROYECTO]` (región us-west-2)
- Esquema: 25 tablas en `public`. Última migración de Alembic: `a9e4c1f7b3d2` (esquema `bi`
  con vistas de solo lectura para Power BI y el rol `lector_bi`; ver `alembic current` para saber
  si ya está aplicada). La anterior, `f7d3b9e5a2c8`, agregó `domos.es_demostracion`.
- Esquema `bi` (solo vistas): lo lee únicamente `lector_bi`, que no tiene ningún permiso en
  `public`. Las vistas NO llevan `security_invoker` (con RLS sin políticas no verían nada) y `bi`
  no se expone en la API de Supabase. Detalle en `docs/PowerBI.md`, sección 7.
  Detalle en `docs/Requisitos/Variables.md`, secciones 16 a 18
- `backend/models.py` refleja las tablas y columnas — no usar `Base.metadata.create_all()`,
  las tablas ya existen en Supabase, solo hay que conectarse. Los ON DELETE reales y los
  índices creados a mano solo están en la base (entre ellos `idx_lecturas_{bateria,consumo,fv,sensores}_domo_fecha`
  e `idx_historial_domo_fecha`, todos `(domo_id, fecha_hora)`; las pruebas de Postgres los
  recrean, ver `INDICES_SUPABASE` en `tests/test_migracion_postgres.py`), y las columnas `Float` son `real` en Postgres: no usar
  `alembic --autogenerate`, que propondría cambios no relacionados. Las migraciones se
  escriben a mano.
- Tablas (25):
  - 13 originales: usuarios, domos, administrador_domo, dispositivos, lecturas_fotovoltaicas,
    lecturas_consumo, lecturas_bateria, lecturas_sensores, alertas, historial_acciones,
    estado_comunicacion, configuracion_sistema, alembic_version
  - 11 catálogos: rol_usuario, rol_actor, tipo_dispositivo, tipo_accion, tipo_alerta,
    nivel_prioridad, estado_alerta, estado_bateria, estado_generacion, tipo_lectura,
    categoria_dispositivo
  - sensores
- RLS activo en las 25 tablas, sin políticas: `anon` y `authenticated` (la API de datos de
  Supabase) no leen ni escriben nada. El backend, el listener y las migraciones conectan
  con el rol `postgres`, dueño de las tablas y con BYPASSRLS, así que no les afecta. Esto
  cierra el acceso externo, pero NO separa un negocio de otro: ver "Corrección sobre RLS".
- Conexión: `backend/database.py` + `backend/.env` (no versionado)

## Categorías de dispositivo y R-13

Cada dispositivo tiene una categoría: `esencial`, `importante`, `confort` o `prescindible`.
La fórmula está solo en `backend/servicios/categorias.py`, y el frontend la obtiene de
`GET /domos/{id}/categorias`; no se debe repetir en otro lado. Detalle en
`docs/Requisitos/Variables.md`, sección 17.

- Modelo v2: los umbrales y la prioridad de cada toma NO se guardan ni se editan: se calculan
  en cada respuesta y en cada evaluación de R-13 con su categoría y la batería y el perfil del
  domo (`servicios/categorias.preset_de`). Enviar umbrales da 422; `personalizado` siempre es
  false. (En v1 se copiaban a columnas y había un modo avanzado: ya no existe.)
- `esencial`: R-13 nunca la corta; el apagado manual sigue permitido. Los bombillos siempre
  son esenciales. El asistente de la app preselecciona `confort` al crear una toma.
- Cada domo tiene un perfil de control (`conservador`, por defecto, `equilibrado` o
  `maximo_uso`) que define cuánto sobre la reserva se apaga cada categoría.
- Cambiar el tipo de batería o el perfil cambia los umbrales de todas las tomas en la siguiente
  evaluación. Sin tipo configurado se usa `lifepo4`.
- R-13 solo decide con un SoC actual: el último con valor, de como mucho 2 minutos, y que la
  lectura de batería más reciente lo traiga. Si no, no corta ni reactiva (`/evaluar` da 409 con
  el motivo; el listener lo registra una vez).
- R-13 solo reactiva tomas cuya última acción es un apagado del sistema: lo que apagó una persona
  no lo vuelve a encender.
- **Primacía de la decisión humana.** Si la última acción sobre una toma
  es un ENCENDIDO de una persona (`actor_tipo` `dueno` o `administrador`), R-13 no la corta por su
  umbral de apagado hasta que la batería se recupere: hasta que, DESPUÉS de esa acción, haya una
  medición con SoC >= el umbral de reactivación de la toma. Desde ahí vuelve a la regla normal.
  **Excepción:** con el SoC actual bajo la reserva de la batería del domo (lifepo4 20, agm 50), la
  corta igual; esa acción guarda `motivo=reserva` en `acciones.condicion` (se ve en el Historial) y
  sale con `motivo: "reserva"` en `/evaluar`. Nada se guarda: sale de `acciones` y `mediciones`
  (`servicios/control_energetico.primacias_humanas`, una consulta por domo). `/evaluar` lista las
  respetadas en `respetadas_por_primacia_humana`, el listener lo registra una vez por toma y por
  encendido, `tomas_por_cortar` no las anuncia y `DispositivoOut.primacia_humana_hasta_soc` lo
  muestra en la app ("Encendida a mano: el sistema la respeta hasta…"). Igual en tomas reales y
  simuladas, y en el generador del domo de demostración.
- `activo = false` desactiva sin borrar: R-13 lo ignora y los listados lo ocultan salvo con
  `?incluir_inactivos=true`.
- Permisos del PATCH de dispositivo: el dueño cambia todo. El administrador solo enciende,
  apaga y maneja la iluminación (brillo, color, temperatura).
- `color` y `temperatura_color` de un bombillo solo aceptan los valores de
  `backend/servicios/iluminacion.py` (422 con las opciones); la app los muestra en selectores con
  `GET /iluminacion/opciones`. Un valor viejo fuera de la lista se sigue leyendo y la app lo
  muestra como "Sin definir". Hoy solo se guardan: no llegan al bombillo real.

`modo_actual` de `GET .../control-energetico/estado`:

| Modo | Cuándo |
|---|---|
| `normal` | R-13 no ha apagado ninguna toma |
| `ahorro` | Todas las tomas apagadas por R-13 son prescindibles. No genera alerta |
| `supervivencia` | R-13 apagó alguna otra toma |

El modo describe solo cortes reales. Lo que todavía no ha pasado va en `tomas_por_cortar`: las
encendidas que ya cruzaron su umbral y se apagarán en la próxima evaluación.

La alerta `bateria_baja` se crea solo si se corta una toma `confort` o `importante`: es
`critico` si SoC <= reserva de la batería del domo (lifepo4 20, agm 50) y si no `advertencia`.

Los "umbrales globales" que devuelven `GET .../configuracion` y `/evaluar` son el preset
confort de la batería y el perfil del domo (en v2 no se guardan ni se editan).

La marca del frontend está en `frontend/src/config/marca.js` (nombre `Vatia`) y los colores en
los tokens `@theme` de `frontend/src/index.css`.

El domo 1 tiene una batería AGM (CSB HRL12390W, VRLA), 1200 Wh, reserva 50 %.

## Cuentas de administrador

Solo el dueño. `POST /auth/registro` crea la cuenta y exige `domo_id` (uno de sus domos: la
cuenta nace asignada a él; en la app, el selector "Domo" de Crear administrador);
`GET /administradores` lista las cuentas con alguna asignación de administrador en sus domos;
`PATCH /administradores/{id}` edita o desactiva solo las que no tienen asignaciones fuera de sus
domos (`cuenta_propia`); `POST` y `PATCH /domos/{id}/administradores` asignan o quitan un domo
(quitar borra la asignación; quitar la última da 409: para cortarle el acceso se desactiva la
cuenta).
Desactivar corta el acceso de inmediato, también en sesiones abiertas. Ninguna respuesta lleva
hashes. `POST /administradores/{id}/restablecer-contrasena` pone una temporal (solo cuentas
propias, `cuenta_propia`) y `POST /users/me/contrasena` cambia la propia. Ambos cierran las sesiones
abiertas: `usuarios.version_sesion` viaja en el JWT, y subirla invalida los tokens anteriores. Un
dueño solo puede asignar administradores que ya tienen alguno de sus domos; una cuenta nueva se crea
con `domo_id`.

Una cuenta creada o restablecida por el dueño tiene contraseña temporal
(`usuarios.debe_cambiar_contrasena`): hasta cambiarla en Mi cuenta, el backend solo responde
`GET /users/me` y `POST /users/me/contrasena`, y el resto da 403. Si el dueño olvida su contraseña:
`python tools/restablecer_contrasena.py` en el PC con `backend/.env` (ver `backend/README.md`).

## Domo de demostración

`domos.es_demostracion = true` marca un domo con datos sintéticos: no es un domo real. Lo crea y
lo llena `backend/tools/generar_datos_demo.py` (lo corre el dueño, ver `backend/README.md`); ningún
endpoint cambia ese campo. La app muestra "Demostración · datos sintéticos" junto al nombre del domo
en todas las páginas con domo y en el selector, y el CSV de Reportes y `dim_domo.csv` llevan la
misma marca. Sus dispositivos no tienen `id_fisico`, así que nada se publica a una toma real.

El generador aplica R-13 con un reloj simulado usando `servicios/control_energetico.decidir_r13`,
la misma función que usa `evaluar_control_energetico`: si cambian las reglas de R-13, cambian en
los dos.

## Reportes y Power BI

`GET /domos/{id}/reportes/resumen` y `backend/tools/exportar_bi.py` calculan con las mismas reglas,
en `backend/servicios/reportes.py`. Días y horas en America/Bogota. Las instrucciones para Power BI
están en `docs/PowerBI.md`. En v2 el esquema `bi` (mismas 9 vistas y columnas que v1) lo crea la
revisión `4e8b2c6d9f17` de `alembic_v2`; `lector_bi` nace sin contraseña.

Energía: Históricos (`GET .../historico`) SUMA `energia_*_wh` tal como llegó; Reportes INTEGRA la
potencia (trapecios, sin integrar huecos de más de 10 min). No tienen por qué coincidir: antes del
2026-10-27 el simulador guardaba la energía en tiempo simulado (acelerado), y eso infla la suma de
Históricos para ese periodo.

## Mensajes de validación

Los 422 de Pydantic se traducen a español en un solo lugar, `backend/errores_validacion.py`.

## Pruebas del backend

Desde `backend/`, con el entorno virtual activo y `pip install -r requirements-dev.txt`:

```
python -m unittest discover -s tests -t .
```

Corren sobre SQLite en memoria (con `PYTHONUTF8=1` en Windows): `tests/__init__.py` fija
`DATABASE_URL_V2` y `DATABASE_URL` antes de importar la aplicación y hace fallar de inmediato cualquier `create_engine` que no sea SQLite ni localhost, así
que no pueden llegar a Supabase. Cada archivo de pruebas importa `tests.base` antes que
`database`, `models` o `main` (`tests/test_bi.py` lo comprueba). Las de Postgres
(`tests/test_esquema_v2_postgres.py` en el modelo v2; `tests/test_migracion_postgres.py` y
`tests/test_bi_postgres.py` son de la línea v1 y aquí se omiten siempre) se omiten salvo que
`TEST_POSTGRES_URL` apunte a un Postgres local vacío y desechable: borran los esquemas `public` y
`bi`, crean y borran roles, y se niegan a correr fuera de `localhost`.

Los cálculos del frontend (autonomía, nivel de batería, series) se prueban sin dependencias: en
`frontend/`, `npm test`.

## Contrato MQTT con el ESP32

Detalle, pines y calibración en `firmware/README.md`. El ESP32 controla varios dispositivos
Tuya (lista en `firmware/domo_nodo/secrets.h`); cada orden lleva el `id_fisico` del dispositivo,
el mismo de `dispositivos.id_fisico` en la app.

Órdenes (las publica `backend/mqtt_publisher.py`, nunca retenidas, qos 1):

```json
domo/{id}/comando/toma      {"accion": "on", "id_fisico": "toma1", "origen": "manual"}
domo/{id}/comando/bombillo  {"id_fisico": "bombillo1", "origen": "manual", "accion": "on", "brillo": 40}
domo/{id}/comando/soc       {"accion": "iniciar"|"confirmar"|"cancelar", "origen": "app"}
```

- Toma: un PATCH con `estado`, o R-13 (`origen: "r13"`).
- Bombillo: un PATCH del dueño o del administrador con `estado`, `brillo` (0-100) o ambos;
  `accion` y `brillo` son opcionales, al menos uno. R-13 nunca toca bombillos. `color` y
  `temperatura_color` todavía no se envían (el firmware solo escribe los DPS 20, encendido, y 22,
  brillo, este último siempre con el 21 en `white`; no usa el 23 ni el 24).
- Solo dispositivos con `id_fisico`: sin él son simulados y no se publica nada.
- SoC: recalibración GUIADA, solo el dueño (`POST /domos/{id}/soc-recalibracion`). Nunca lleva un
  valor de SoC: la ESP32 mide en reposo 15 min, propone el valor de la tabla y el dueño confirma.
  La ESP32 responde en `domo/{id}/estado/soc` (retenido; `inactiva` al arrancar). La API lo conoce
  por la suscripción de su cliente MQTT (`servicios/estado_soc.py`, en memoria, sin tabla) y el
  listener solo lo registra en el log. Ver `firmware/README.md`, "Recalibración guiada del SoC".

Estado (lo publica el firmware, retenido, después de cada orden). El backend todavía no lo
consume:

```json
domo/{id}/estado/toma/toma1          {"id_fisico": "toma1", "estado": "on", "verificado": true}
domo/{id}/estado/bombillo/bombillo1  {"id_fisico": "bombillo1", "estado": "on", "verificado": true, "brillo": 40}
domo/{id}/estado/toma/toma2          {"id_fisico": "toma2", "estado": "off", "verificado": false, "error": "sin_conexion_toma"}
```

- `verificado: true`: el dispositivo confirmó lo pedido.
- `verificado: false`: fallaron los 3 intentos (`sin_conexion_toma` o `sin_conexion_bombillo`).
  `estado` es el último CONFIRMADO, no el pedido, y `desconocido` si aún no hay ninguno.
  `id_fisico_desconocido`: el id no está en `secrets.h`. `ip_desconocida`: la IP va en 0 en
  `secrets.h` (se toma la que el dispositivo anuncia por UDP) y todavía no se ha anunciado.
- Antes había un solo tema, `domo/{id}/estado/toma`, sin `id_fisico`: con varias tomas, un
  mensaje retenido por tema pisaría el de las otras.

Telemetría (cada 10 s, sin retener; la guarda `backend/mqtt_listener.py`, en v2 en `mediciones`): los temas
`domo/{id}/telemetria/{fotovoltaica,bateria,consumo,sensores}` con los campos de
`MAPEO_TIPOS`. La potencia del panel y la corriente de la batería son ESTIMADAS (no se mide el
voltaje del panel), y un sensor sin respuesta va como `null`. Ver `firmware/README.md`.

**El simulador (`backend/tools/simulador_telemetria.py`) NUNCA se corre sobre un domo cuyo ESP32
publica telemetría real**: las dos fuentes se mezclarían en las mismas tablas. El simulador lo
protege así:
- `--domo` es obligatorio: no hay domo por defecto.
- Antes de publicar consulta la base, solo leyendo. Si el domo tuvo telemetría en los últimos
  10 minutos, o si no puede comprobarlo (`--sin-bd`, base caída), se niega a correr.
- `--forzar` salta la comprobación. Ver `backend/README.md`.

## Decisión de arquitectura: multi-tenencia (multi-negocio)

**Contexto:** el objetivo del proyecto se amplió — no es solo un domo experimental
para la tesis, la meta es ofrecer la aplicación a varios negocios de glamping
independientes entre sí. Esto NO requirió cambiar el esquema de base de datos,
pero sí cambia la prioridad de ciertas piezas de seguridad.

### Lo que ya funciona como límite de negocio (sin cambios)
- `domos.propietario_id` ya actúa como el límite entre un negocio y otro — cada
  domo pertenece a exactamente un usuario con rol `dueño`.
- Todo lo que cuelga de un domo (dispositivos, lecturas_*, alertas,
  historial_acciones, configuracion_sistema) queda lógicamente aislado por
  esa misma relación. No se necesita una tabla nueva de "negocios" o "tenants".

### Regla que se mantiene igual (RF-02)
- La creación de una cuenta `dueño` sigue siendo exclusivamente manual, vía
  Supabase directamente — NO existe ni existirá un endpoint público de
  autoregistro de dueños. Es una decisión deliberada, no una limitación
  pendiente de resolver.

### Lo que SÍ cambia de prioridad: Row Level Security (RLS)
- RLS deja de ser "antes de salir a producción" (nice-to-have) y pasa a ser
  **obligatorio antes de dar de alta al segundo negocio real** en la base de
  datos compartida. Sin RLS, un error en cualquier endpoint del backend podría
  filtrar datos de un dueño a otro.
- RLS se debe diseñar e implementar EN PARALELO con R-3 (gestión de múltiples
  domos) y R-4 (asignación de administradores) — no después, porque esas son
  justo las tablas que definen el límite entre negocios.

### Regla para todo endpoint nuevo, a partir de ahora
- Ningún endpoint puede filtrar solo por `rol` (dueño/administrador). Todo
  endpoint que devuelva o modifique datos de un domo, dispositivo, lectura,
  alerta, etc. debe verificar además que el domo pertenezca al usuario
  autenticado (directamente si es dueño, o vía `administrador_domo` si es
  administrador) — nunca asumir que "es dueño" es suficiente para ver
  cualquier domo del sistema.

### Corrección sobre RLS (reemplaza la suposición anterior)

El backend usa autenticación propia (JWT, R-1) con una única conexión
compartida a Postgres (DATABASE_URL) — NO usa Supabase Auth. Por lo tanto,
`auth.uid()` no tiene ningún valor útil en este proyecto, y las políticas de
RLS estándar de Supabase NO aplican tal como se documentan por defecto.

La protección de acceso por dueño/administrador debe implementarse en el
backend (FastAPI), filtrando explícitamente cada consulta por
`propietario_id` (dueño) o vía `administrador_domo` (administrador).

RLS real con multi-tenencia queda pendiente como tarea aparte, a futuro,
usando variables de sesión personalizadas de Postgres en vez de `auth.uid()`.