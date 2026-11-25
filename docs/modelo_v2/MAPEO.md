# Modelo de datos v2 — mapeo desde v1, esquema y copia

Ronda DB-1. Base: `docs/modelo_v2/esquema_prueba.sql`,
versión A de mediciones. En esta ronda no cambia el backend, el listener, el simulador, el frontend
ni el firmware: solo el esquema v2, este mapeo, el script de copia y sus pruebas.

| Pieza | Archivo |
|---|---|
| Configuración de Alembic v2 | `backend/alembic_v2.ini`, `backend/alembic_v2/env.py` |
| Revisión base (crea las 7 tablas) | `backend/alembic_v2/versions/2b8e4f1a6c90_esquema_base_v2.py` |
| Segunda revisión (DB-2): `voltaje_fv_v` y `temperatura_bateria_c` | `backend/alembic_v2/versions/7c3d9a1e4b52_mediciones_voltaje_fv_y_temperatura.py` |
| Tercera revisión (DB-3): esquema `bi` y rol `lector_bi` | `backend/alembic_v2/versions/4e8b2c6d9f17_esquema_bi_v2.py` |
| Copia v1 → v2 | `backend/tools/copiar_a_v2.py` |
| Pruebas sin base | `backend/tests/test_copiar_a_v2.py` |
| Pruebas con Postgres local | `backend/tests/test_esquema_v2_postgres.py` (solo con `TEST_POSTGRES_URL`) |

```
usuarios ──< asignaciones >── domos ──< dispositivos ──< acciones
                                │
                                ├──< alertas
                                └──< mediciones
```

Siete tablas y seis claves foráneas: es un árbol, así que entre dos tablas hay un solo camino
(`tests/test_copiar_a_v2.py` lo comprueba sobre el SQL de la migración). No hay tablas de catálogo:
los valores permitidos son `CHECK`.

---

## 1. Esquema v2: diferencias con `esquema_prueba.sql` y por qué

Cada cambio responde a la prioridad de la ronda: que la app no falle el martes 2026-11-17.

| Cambio | Por qué |
|---|---|
| Textos en `TEXT` en vez de `VARCHAR(n)` | v1 usa `text` sin límite y la API no limita el largo de nombres, correos, mensajes, etc. Con `VARCHAR(100)`, una entrada que hoy se acepta daría un 500. Donde la API sí limita (`dispositivos.ubicacion`, 100) va un `CHECK (char_length(...) <= 100)`. |
| Medidas, umbrales de alerta y capacidad en `double precision` en vez de `NUMERIC(p,s)` | v1 usa `real` y todo el código las trata como `float` (`Mapped[float]`, `cast(..., Float)` en reportes). Con `NUMERIC` SQLAlchemy devuelve `Decimal` y aparecerían errores de tipos. Además `NUMERIC(6,3)` no cabe una corriente de 1000 A, que el listener hoy acepta: la fila entera fallaría. |
| Rangos de `mediciones` = `schemas.LIMITES_TELEMETRIA` (p. ej. `potencia_fv_w` de -100 a 100 000, no `>= 0`) | En la tabla ancha, un solo valor fuera del `CHECK` hace fallar la fila completa, SoC incluido, y R-13 no se evalúa. El listener ya convierte en NULL lo que está fuera de esos límites, así que con los mismos rangos nunca choca. -100 W es el margen de ruido del sensor alrededor de cero que v1 acepta. `tests/test_copiar_a_v2.py` compara los rangos con `LIMITES_TELEMETRIA`. |
| `domos.reserva_pct` **no** está | Se calcula: es `RESERVA_SOC_POR_BATERIA[bateria_tipo]` (lifepo4 20, agm 50) en `servicios/categorias.py`, y nadie la puede fijar aparte. Guardarla sería un segundo lugar para el mismo dato (L3). Confirmado por David (decisión 1). |
| `domos.bateria_tipo` NOT NULL DEFAULT `'lifepo4'`, `domos.perfil` NOT NULL DEFAULT `'conservador'`, `domos.bateria_capacidad_wh` **nullable** | Son los valores que el código ya usa cuando no hay nada guardado (`tipo_bateria_de`, `perfil_de`). La capacidad no tiene valor por defecto en v1: si no está, la app no la muestra. Ponerle uno sería inventar un dato. |
| `usuarios.desactivado_en` y su `CHECK (activo OR desactivado_en IS NOT NULL)` **no** están | Nadie lo usa y v1 no guarda esa fecha: la copia tendría que inventarla para cada cuenta inactiva. |
| `usuarios.apellido` y `usuarios.telefono` | Los usan `POST /auth/registro`, `PATCH /administradores/{id}`, `/users/me` y las páginas Administradores, Mi cuenta y el menú (`Layout.jsx`). |
| `dispositivos.ubicacion`, `color`, `temperatura_color` | La API y el frontend los leen y escriben (asistente y panel del dispositivo, tarjeta, saludo del Panel). Quitarlos rompe esas pantallas. |
| `dispositivos.desactivado_en` con `CHECK (activo = (desactivado_en IS NULL))` | `servicios/reportes.py`, `tools/exportar_bi.py` y `bi.v_cortes` terminan con él el tiempo que una toma cuenta como cortada por R-13. El PATCH ya lo pone al desactivar y lo vacía al reactivar, y un dispositivo nuevo siempre nace activo, así que el `CHECK` no rompe nada. |
| `mediciones`: `voltaje_bateria_v`, `corriente_bateria_a`, `potencia_bateria_w`, `corriente_fv_a`, `energia_fv_wh`, `energia_consumo_wh`, `frecuencia_hz`, `factor_potencia`; sin `energia_kwh`. En DB-2 (segunda revisión), `voltaje_fv_v` y `temperatura_bateria_c` | Son los campos que el firmware publica (`firmware/README.md`, "Qué publica"). `potencia_bateria_w` la usa el Panel (flujo de energía). `energia_*_wh` es la energía **del intervalo** que calcula el ESP32 con muestreo fino y que suma `/historico`; no se puede recalcular igual desde muestras de 10 s. `corriente_fv_a` es la única medición real del panel (la potencia es estimada). Frecuencia y factor de potencia los usa `bi.v_pzem`. El PZEM no publica un contador acumulado (`energia_kwh` no tendría origen). DB-2: toda medida que publiquen el firmware **o el simulador** tiene columna, aunque ninguna pantalla la muestre (datos para el documento de grado y Power BI); por eso se agregaron el voltaje de los paneles y la temperatura de la batería, con los rangos de `LIMITES_TELEMETRIA`. |
| Índice `acciones (dispositivo_id, fecha_hora DESC, id DESC)` | R-13 busca la última acción de cada toma con desempate por id (`_ultima_accion_de_dispositivo`). |
| Guarda al inicio de la migración | Si `DATABASE_URL_V2` apunta por error a la base v1 (tiene `historial_acciones`, `lecturas_bateria`…), falla sin crear nada. `alembic_v2/env.py` además se niega si `DATABASE_URL_V2` y `DATABASE_URL` son la misma base. |
| RLS activado en las 7 tablas y en `alembic_version` | Igual que v1: el backend entra como `postgres` (BYPASSRLS) y la API de datos de Supabase no ve nada. |

Sin cambios respecto a `esquema_prueba.sql`: `asignaciones` con `UNIQUE (usuario_id, domo_id)` y el
índice único parcial de un dueño por domo; `acciones` cuelga solo de `dispositivos` con
`actor_tipo`/`actor_ref` sin FK; `alertas` con `id_fisico_ref` sin FK, `creada_por_r13` y el `CHECK`
de atendida con fecha; `mediciones` con `UNIQUE (domo_id, fecha_hora)`; `ON DELETE RESTRICT` para
dispositivos y acciones y `CASCADE` desde domos para asignaciones, alertas y mediciones.

"Un domo tiene al menos un dueño" no se puede exigir con un `CHECK`: lo debe garantizar el backend al
crear el domo (DB-2) y la copia lo revisa (`DOMO_SIN_DUENO`).

---

## 2. Mapeo tabla por tabla, columna por columna

"→" = a dónde va en v2. "✗" = desaparece, con el motivo.

### `usuarios` → `usuarios` (+ el rol pasa a `asignaciones`)

| v1 | v2 |
|---|---|
| `id`, `nombre`, `apellido`, `correo`, `contrasena_hash`, `telefono`, `version_sesion`, `debe_cambiar_contrasena` | igual |
| `estado_cuenta` (`activo`/`inactivo`) | → `activo` (boolean) |
| `fecha_creacion` | → `creado_en` |
| `rol` | ✗ → `asignaciones.rol`, por domo. Un dueño de v1 queda `dueno` de cada domo cuyo `propietario_id` es él |
| `creado_por_id` | ✗ decidido en el modelo. Hoy define qué administradores puede editar un dueño (`cuenta_propia`); en v2 lo define tener una asignación en alguno de sus domos. Ver DB-2 y decisión 7 |
| `fotografia`, `fecha_ultimo_acceso`, `estado_sesion` | ✗ nadie los lee ni los escribe |
| `token_autenticacion` | ✗ nadie lo usa: la sesión va en el JWT y se invalida con `version_sesion` |

### `domos` + `configuracion_sistema` → `domos`

| v1 | v2 |
|---|---|
| `domos.id`, `nombre`, `ubicacion`, `es_demostracion` | igual |
| `domos.fecha_registro` | → `creado_en` |
| `domos.propietario_id` | ✗ → `asignaciones (usuario_id, domo_id, 'dueno')` |
| `domos.descripcion`, `estado_domo`, `estado_conexion` | ✗ el frontend no los usa; `estado_domo` no filtra nada en el backend (solo se devuelve) y `estado_conexion` nunca se escribe |
| `configuracion_sistema.parametros_energeticos.bateria.tipo` | → `domos.bateria_tipo` (sin valor válido: `lifepo4`, como hoy) |
| `…bateria.capacidad_wh` | → `domos.bateria_capacidad_wh` (sin batería válida: NULL, como hoy) |
| `…perfil_control` | → `domos.perfil` (sin valor válido: `conservador`, como hoy) |
| `…control_energetico` (umbrales globales) | ✗ en v2 cada toma usa el preset de su categoría y perfil. Ver "Umbrales" en la sección 4 |
| `configuracion_sistema.parametros_control`, `limites_alerta`, `prioridades_carga`, `configuracion_dispositivos` | ✗ texto libre sin formato ni consumidor (lo dice `routers/configuracion.py`); el frontend no los usa |
| `configuracion_sistema.id`, `domo_id`, `fecha_modificacion` | ✗ la fila se fusiona en `domos` (1:1) |

### `administrador_domo` → `asignaciones`

| v1 | v2 |
|---|---|
| `id` | igual (las de dueño, que no existían, se numeran después del mayor) |
| `administrador_id`, `domo_id` | → `usuario_id`, `domo_id`, `rol = 'administrador'` |
| `estado_asignacion` | ✗ en v2 quitar el acceso es borrar la fila; las no activas no se copian (`ASIGNACION_NO_ACTIVA`) |
| `permisos_asignados` | ✗ texto libre que nada interpreta |
| `fecha_asignacion` | ✗ solo se devolvía en la API; el frontend no lo usa |

### `dispositivos` + `estado_comunicacion` → `dispositivos`

| v1 | v2 |
|---|---|
| `id`, `domo_id`, `tipo`, `nombre`, `ubicacion`, `categoria`, `estado`, `brillo`, `color`, `temperatura_color`, `activo`, `desactivado_en`, `id_fisico`, `ultima_comunicacion` | igual (`tipo` solo `toma`/`bombillo`: `otro` no existe en v2) |
| `estado_conexion` (texto) | → `conectado` (boolean: `estado_conexion = 'conectado'`, lo mismo que cuenta `estado_general`) |
| `prioridad` | ✗ se calcula de la categoría (`PRIORIDAD_POR_CATEGORIA`) |
| `umbral_apagado_soc`, `umbral_reactivacion_soc` | ✗ se calculan con categoría + batería + perfil del domo (`preset_de`). Se pierden los personalizados (`UMBRALES_PERSONALIZADOS`) |
| `potencia`, `energia_consumida` | ✗ nadie los escribe |
| `estado_comunicacion.estado_conexion`, `ultima_comunicacion` | → `dispositivos.conectado`, `ultima_comunicacion`: el dato más reciente entre la tabla y la columna del dispositivo |
| `estado_comunicacion.senal`, `identificador_dispositivo`, `estado_mqtt` y el historial | ✗ v2 guarda solo el último estado; el frontend no usa `/estado-comunicacion` |

### `historial_acciones` → `acciones`

| v1 | v2 |
|---|---|
| `id`, `dispositivo_id`, `condicion_control` (→ `condicion`), `resultado`, `fecha_hora` | igual |
| `accion_realizada`, `valor_anterior`, `valor_nuevo` | → `accion`, `estado_anterior`, `estado_nuevo` |
| `rol` (`dueño`/`administrador`/`sistema`) | → `actor_tipo` (`dueno`/`administrador`/`sistema`). `cliente` no existe en v2 |
| `usuario_id` | ✗ → `actor_ref` = correo del usuario; `R-13` si `rol = 'sistema'`. Texto sin FK: el historial no depende de que la cuenta siga igual |
| `domo_id` | ✗ sale por JOIN con `dispositivos` |

### `alertas` → `alertas`

| v1 | v2 |
|---|---|
| `id`, `domo_id`, `valor_detectado`, `fecha_hora`, `fecha_atendida` | igual |
| `tipo_alerta`, `nivel_prioridad`, `estado_alerta` | → `tipo`, `nivel`, `estado` (NOT NULL en v2) |
| `descripcion` | → `mensaje` (NOT NULL) |
| `variable_asociada`, `valor_limite` | → `variable`, `limite` |
| — | `creada_por_r13` = la descripción empieza por `"R-13 cortó cargas: "` (así las reconoce hoy `_atender_alertas_corte`) |
| — | `id_fisico_ref`: NULL (v1 no asocia alertas a dispositivos) |

### `lecturas_*` + `sensores` → `mediciones`

Una fila por domo y bloque de 10 s; `fecha_hora` es el inicio del bloque, en UTC.

| v1 | v2 |
|---|---|
| `lecturas_bateria.estado_carga_soc`, `estado_bateria`, `voltaje`, `corriente`, `potencia` | → `soc_pct`, `estado_bateria`, `voltaje_bateria_v`, `corriente_bateria_a`, `potencia_bateria_w` |
| `lecturas_bateria.temperatura` | → `temperatura_bateria_c` (revisión `7c3d9a1e4b52`). El firmware no la manda (no hay sensor); sí el simulador y el generador |
| `lecturas_fotovoltaicas.corriente`, `potencia_generada`, `energia_generada` | → `corriente_fv_a`, `potencia_fv_w`, `energia_fv_wh` |
| `lecturas_fotovoltaicas.voltaje` | → `voltaje_fv_v` (revisión `7c3d9a1e4b52`). El firmware lo manda en `null` (no se mide; el GPIO33 quedó libre para eso); el simulador sí lo manda |
| `lecturas_fotovoltaicas.estado_generacion` | ✗ se calcula con el mismo umbral del firmware: `generando` por encima de 5 W (`servicios/mediciones.UMBRAL_GENERANDO_W`) |
| `lecturas_consumo.voltaje`, `corriente`, `potencia_consumida`, `energia_consumida` | → `voltaje_ac_v`, `corriente_ac_a`, `potencia_consumo_w`, `energia_consumo_wh` |
| `lecturas_consumo.dispositivo_id` | ✗ una lectura por dispositivo no es el consumo del domo: no se copia (`CONSUMO_POR_DISPOSITIVO`) |
| `lecturas_sensores` con `variable_medida` `frecuencia` / `factor_potencia` | → `frecuencia_hz` / `factor_potencia` |
| `lecturas_sensores.tipo_sensor`, `unidad_medida`, `estado` | ✗ la unidad va en el nombre de la columna |
| `sensor_id` (las cuatro tablas) y la tabla `sensores` | ✗ ningún endpoint ni el firmware los usa |
| `id` de cada lectura | ✗ las filas de `mediciones` son nuevas |

### Tablas que desaparecen sin destino

Los 11 catálogos (`rol_usuario`, `rol_actor`, `tipo_dispositivo`, `tipo_accion`, `tipo_alerta`,
`nivel_prioridad`, `estado_alerta`, `estado_bateria`, `estado_generacion`, `tipo_lectura`,
`categoria_dispositivo`) pasan a `CHECK`. Las descripciones de `categoria_dispositivo` ya están en el
frontend y en `servicios/categorias.py`. `alembic_version` es propia de cada base.

---

## 3. Cómo correr la migración v2

Desde `backend/`, con el `.venv` activado y `DATABASE_URL_V2` en `backend/.env` (la pone David; nunca
se versiona):

```
alembic -c alembic_v2.ini upgrade head      # crea las 7 tablas y agrega las 2 columnas de DB-2
alembic -c alembic_v2.ini current           # debe decir 4e8b2c6d9f17 (head)
alembic -c alembic_v2.ini upgrade head --sql  # opcional: ver el SQL sin conectarse
```

- `alembic` a secas (sin `-c`) sigue siendo la línea v1 con `DATABASE_URL`: no se mezclan.
- `downgrade base` elimina las 7 tablas con sus datos. Solo para una base de pruebas.
- El esquema `bi` y el rol `lector_bi` son de DB-3.

---

## 4. Script de copia (`backend/tools/copiar_a_v2.py`)

```
python tools/copiar_a_v2.py <carpeta_respaldo>                 # ensayo: no se conecta a nada
python tools/copiar_a_v2.py <carpeta_respaldo> --detalle       # ensayo con todos los id
python tools/copiar_a_v2.py <carpeta_respaldo> --aplicar --aceptar CODIGO,CODIGO
```

- Lee la carpeta de `tools/respaldo_bd.py`. Se niega si falta `_manifiesto.json`, si falta una tabla o
  una columna que lee, o si un `.json` no tiene las filas que dice el manifiesto.
- El ensayo muestra filas por tabla v2, los datos de v1 que no pasan (por columna) y cada caso con su
  código, cuántas filas, algunos id y qué hace la copia.
- `--aplicar` se niega si queda algún caso **de decisión** sin `--aceptar`. Luego comprueba que el
  destino esté en la última revisión (`4e8b2c6d9f17` desde DB-3), sin tablas v1 y con las 7 tablas vacías, y escribe todo en una sola
  transacción. Conserva los `id` originales (`OVERRIDING SYSTEM VALUE`) y ajusta cada secuencia al
  mayor id copiado.
- La salida solo tiene cuentas, códigos e id: nunca hashes, tokens, correos, nombres ni la URL. Un
  error de Postgres se muestra solo con su código, tabla y restricción, porque su texto puede traer la
  fila completa.

### Bloques de 10 s

Cada lectura cae en el bloque `floor(epoch / 10) * 10`. Dentro de un bloque, cada fuente (tabla de
lecturas, o variable en `lecturas_sensores`) puede tener varias lecturas (decisión 4 de David):

- **Valores instantáneos** (SoC, estado de la batería, voltajes, corrientes, potencias, frecuencia,
  factor de potencia): los de la **última** lectura (por `fecha_hora` y luego `id`), entera. Si la
  última trae un campo en `null`, queda `null` aunque una anterior lo tuviera.
- **Energía del intervalo** (`energia_fv_wh`, `energia_consumo_wh`): la **suma** de las energías
  válidas de todas las lecturas del bloque, porque cada una cubre su propio intervalo. Los `null` no
  suman; un valor fuera de rango, NaN o Infinity queda fuera de la suma (`MEDIDA_FUERA_DE_RANGO`). Si
  ninguna trae energía, queda `null`.

Las lecturas reemplazadas se cuentan (`LECTURA_REEMPLAZADA`) y el informe dice cuántos Wh de ellas se
sumaron a su bloque.

### Casos

**De decisión** (cambian o descartan datos; `--aplicar` exige aceptarlos):

| Código | Qué es | Qué hace la copia |
|---|---|---|
| `USUARIO_ESTADO` | `estado_cuenta` distinto de `activo`/`inactivo` | se copia desactivado |
| `USUARIO_FECHA_NULA` | usuario sin `fecha_creacion` | `creado_en` = fecha del respaldo |
| `DOMO_SIN_DUENO` | `propietario_id` que no está en usuarios | domo sin asignación de dueño |
| `DOMO_DUENO_ROL_ADMIN` | el propietario tiene rol `administrador` en v1 | queda `dueno` de ese domo |
| `DOMO_FECHA_NULA` | domo sin `fecha_registro` | `creado_en` = fecha del respaldo |
| `CONFIG_DUPLICADA` | varias filas de `configuracion_sistema` para un domo | se usa la de id menor |
| `ASIGNACION_NO_ACTIVA` | `estado_asignacion` distinto de `activo` | no se copia |
| `ASIGNACION_DEL_DUENO` | asignación de administrador sobre su propio domo | no se copia (queda la de dueño) |
| `ASIGNACION_ROL_DUENO` | asignación de administrador a alguien con rol `dueño` (en v1 no le daba acceso) | se copia: gana acceso a ese domo |
| `ASIGNACION_DUPLICADA` | dos asignaciones activas iguales | se copia la de id menor |
| `REFERENCIA_ROTA` | fila que apunta a un usuario, domo o dispositivo que no está | no se copia |
| `DISPOSITIVO_INVALIDO` | tipo (`otro`), categoría o estado fuera de los `CHECK` | no se copia, ni sus acciones |
| `BOMBILLO_NO_ESENCIAL` | bombillo con otra categoría | se copia como `esencial` |
| `BRILLO_INVALIDO` | toma con brillo, o brillo fuera de 0-100 | brillo NULL |
| `DESACTIVADO_INCOHERENTE` | activo con `desactivado_en`, o inactivo sin él | activo: NULL; inactivo: fecha de su última acción o la del respaldo (regla de `e6c2a8d4f1b9`) |
| `ACCION_SIN_DISPOSITIVO` | acción con `dispositivo_id` NULL (en v2 no tendría domo) | no se copia |
| `ACCION_ACTOR_INVALIDO` | rol NULL, `cliente` u otro | no se copia |
| `ACCION_SIN_USUARIO` | acción de dueño/administrador sin usuario (no hay correo) | no se copia |
| `ACCION_VALORES_INVALIDOS` | acción, estados, resultado o fecha nulos o fuera de catálogo | no se copia |
| `ACCION_DE_DISPOSITIVO_OMITIDO` | acción de un dispositivo que no se copia | no se copia |
| `ALERTA_INVALIDA` | tipo, nivel, estado, descripción o fecha nulos o fuera de catálogo | no se copia |
| `ALERTA_ATENDIDA_SIN_FECHA` | `atendida` sin `fecha_atendida` (anteriores a `d5b1f3a9c2e7`) | se copia como `cerrada` |
| `CONSUMO_POR_DISPOSITIVO` | `lecturas_consumo` con `dispositivo_id` | no se copia |
| `LECTURA_SIN_FECHA` | lectura sin fecha o con una ilegible | no se copia |
| `SENSOR_SIN_COLUMNA` | `lecturas_sensores` de otra variable | no se copia |
| `MEDIDA_FUERA_DE_RANGO` | valor fuera del `CHECK` de `mediciones`, NaN o Infinity | ese valor NULL; el resto de la fila se copia |

**Informativos** (la copia sigue):

| Código | Qué es |
|---|---|
| `USUARIO_SIN_DOMO` | usuario que no queda en ningún domo (no ve nada y ningún dueño lo ve) |
| `CONFIG_POR_DEFECTO` | domo sin tipo de batería o perfil válidos: `lifepo4` / `conservador`, como hoy |
| `CONFIG_SIN_CAPACIDAD` | domo sin batería válida guardada: capacidad NULL, como hoy |
| `UMBRALES_GLOBALES` | domo con umbrales globales guardados: se descartan |
| `UMBRALES_PERSONALIZADOS` | toma con umbrales propios distintos del preset: en v2 usa el preset |
| `UMBRALES_DE_GLOBALES` | toma no esencial sin umbrales propios (seguía los globales): en v2 usa el preset |
| `COMUNICACION_HISTORIAL` | filas de `estado_comunicacion`: solo la más reciente de cada dispositivo cuenta |
| `LECTURA_REEMPLAZADA` | lectura con otra posterior de la misma fuente en el bloque: sus instantáneos se descartan y su energía se suma |
| `FECHA_SIN_ZONA` | fecha sin zona en el respaldo: se toma como UTC |

Además, "Datos de v1 que no pasan a v2" cuenta, por columna, las filas que tenían un valor en una
columna que desaparece (por ejemplo `usuarios.token_autenticacion`, `lecturas_sensores.estado`).

Desde DB-2 la copia se niega a copiar un respaldo cuyo manifiesto diga `"origen": "v2"`
(`tools/respaldo_bd.py --origen v2`); uno sin ese dato es anterior a la opción y por lo tanto de v1.
Y el informe siempre dice cuántos **administradores quedan sin ningún domo** (con sus id). Son los
usuarios con rol `administrador` en v1 que no tienen ninguna asignación en v2, sobre todo por
`ASIGNACION_NO_ACTIVA`. Se cuentan aparte de `USUARIO_SIN_DOMO`, que incluye también a los dueños.

---

## 5. Qué hay que cambiar en DB-2 y DB-3

Tamaños: **S** < 1 h, **M** 1–3 h, **L** 3–6 h.

### Cómo se calculan los umbrales sin guardarlos

`servicios/categorias.preset_de(categoria, domo.bateria_tipo, domo.perfil)` ya es la única fórmula. En
v2 R-13 la llama para cada toma en cada evaluación en vez de leer columnas; la prioridad sale de
`PRIORIDAD_POR_CATEGORIA`. Para no tocar el frontend más de lo necesario, `DispositivoOut` sigue
trayendo `prioridad`, `umbral_apagado_soc` y `umbral_reactivacion_soc` calculados, y `personalizado`
siempre `false`. Un PATCH con umbrales responde 422. `GET /configuracion` devuelve en
`umbral_soc_bajo`/`umbral_soc_recuperacion` el preset confort calculado, y un PUT con ellos responde
422. Cambiar batería o perfil ya no recalcula nada: los umbrales cambian solos.

### Cómo queda el rol en el token

El JWT sigue con `sub` y `ver` (`version_sesion`). `rol` deja de ser un dato de la cuenta: se calcula
al iniciar sesión como `dueño` si la cuenta es dueña de al menos un domo y `administrador` si no, solo
para el menú del frontend. Se recomienda que la API siga respondiendo `dueño` (con ñ), aunque la base
guarde `dueno`, para no tocar `Layout.jsx`/`AuthContext.jsx`. **Ningún permiso se decide con el rol del
token**: cada endpoint de un domo mira la asignación de ese usuario en ese domo (`dueno` →
todo; `administrador` → lo operativo). `require_dueno` pasa a ser "dueño de este domo", y para
`/auth/registro` y `/administradores` (sin domo en la URL), "dueño de al menos un domo".

### Backend (DB-2)

| Archivo / función | Cambio | Tamaño |
|---|---|---|
| `models.py` | Reescribir: 7 clases (`Usuario`, `Domo`, `Asignacion`, `Dispositivo`, `Accion`, `Alerta`, `Medicion`), sin catálogos | M |
| `database.py` | Leer la URL de v2 (o renombrar variables al cambiar de base) | S |
| `security.py` | `crear_token_acceso` con rol calculado; `get_current_user` usa `activo`; `require_dueno` → helpers por domo | M |
| `routers/domos.py` (`_domos_accesibles`) | Un solo JOIN con `asignaciones`; crear domo crea la asignación `dueno` en la misma transacción; quitar `descripcion`/`estado_domo` de las salidas | M |
| `routers/asignaciones_domo.py` | Asignar = INSERT, quitar = DELETE; sin `estado_asignacion`/`permisos_asignados` (PATCH de estado → DELETE o 422) | M |
| `routers/administradores.py`, `routers/auth.py` | Sin `creado_por_id`: lista = administradores con asignación en mis domos; `POST /auth/registro` exige `domo_id` y crea la asignación en la misma transacción (decisión 7); `estado_cuenta` → `activo` | M |
| `routers/users.py`, `tools/restablecer_contrasena.py` | `estado_cuenta` → `activo`; rol calculado | S |
| `routers/configuracion.py` | Lee/escribe columnas de `domos`; sin umbrales globales ni campos de texto libre | M |
| `servicios/categorias.py` | Lee `domo.bateria_tipo`/`domo.perfil`; quitar `recalcular_tomas`, `es_personalizado`, umbrales globales | M |
| `servicios/control_energetico.py` | Umbrales con `preset_de`; última acción por `actor_tipo = 'sistema'`; acciones con `actor_ref`; SoC de la última `medicion` con `soc_pct` no nulo; alertas con `creada_por_r13` | M |
| `routers/control_energetico.py`, `routers/categorias.py` | Ajustar a lo anterior | S |
| `routers/dispositivos.py` | Sin umbrales/prioridad guardados; acción manual con `actor_tipo` según la asignación y `actor_ref` = correo | M |
| `routers/historial.py` | JOIN con `dispositivos` para el domo; mapear `actor_tipo` → `rol` en la salida para el frontend | S |
| `routers/alertas.py` | Nombres de columna; `tipo`, `nivel` y descripción obligatorios en `AlertaCreate` | S |
| `routers/lecturas_{bateria,fotovoltaicas,consumo,sensores}.py` | Leer y escribir `mediciones` manteniendo las mismas rutas y formas de salida (el frontend usa `/lecturas-*`) | M |
| `routers/estado_general.py`, `routers/historico.py` | Última `medicion` y agregados sobre `mediciones`; `conectado` | M |
| `routers/estado_comunicacion.py` | El POST actualiza `dispositivos.conectado/ultima_comunicacion`; el GET devuelve solo el último | S |
| `servicios/reportes.py` | `intervalos()` sobre una sola tabla con la columna que toque; cortes con `acciones` | M |
| `schemas.py`, `errores_validacion.py` | Quitar/renombrar campos; `LIMITES_TELEMETRIA` sigue siendo la fuente de los rangos | M |
| `mqtt_listener.py` | `INSERT … ON CONFLICT (domo_id, fecha_hora) DO UPDATE` solo con las columnas del tema, con `fecha_hora` truncada al bloque de 10 s; `estado_generacion`, `sensor_id`, `dispositivo_id`, `temperatura` se ignoran; R-13 igual tras el tema de batería | M |
| `tools/simulador_telemetria.py` | `leer_umbrales_de_la_base` con `preset_de`; la comprobación de "telemetría reciente" sobre `mediciones` | S |
| `tools/generar_datos_demo.py` | Escribir las 7 tablas v2 (mediciones agrupadas, acciones con `actor_ref`) | L |
| `tools/exportar_bi.py` | Consultas sobre v2 | M |
| `tools/respaldo_bd.py` | Sirve igual para v2 (es genérico); solo cambia la variable de URL si se respalda la v2 | S |
| Pruebas: `tests/base.py` y las ~15 de API | Fixtures con asignaciones y mediciones; quitar las de umbrales personalizados y globales | L |

### DB-3

| Pieza | Cambio | Tamaño |
|---|---|---|
| Migración v2 del esquema `bi` y `lector_bi` | Reescribir las 9 vistas sobre v2 (más simples: una sola tabla de mediciones; `v_domos` sin JSON) | L |
| `tests/test_bi.py`, `tests/test_bi_postgres.py`, `tools/probar_lector_bi.py` | Ajustar a las vistas v2 | M |
| Frontend: `PanelDispositivo.jsx`, `TarjetaDispositivo.jsx`, `components/dispositivos/categorias.js` | Quitar el modo avanzado (umbrales propios) y la marca "Personalizado" | S |
| Frontend: `Configuracion.jsx`, `TarjetaSupervivencia.jsx` | Quitar la edición de umbrales globales; mostrar los del preset | S |
| Frontend: `Administradores.jsx` | Quitar/reactivar = borrar/crear la asignación; sin `estado_asignacion` | S |
| Firmware | Nada: el contrato MQTT no cambia | — |
| `docs/arquitectura_sistema.md`, `backend/README.md`, `docs/PowerBI.md` | Describir v2 | S |

---

## 6. Decisiones de David (respuestas a las preguntas de DB-1)

1. **`reserva_pct`**: no se guarda. La reserva sigue derivada del tipo de batería
   (`RESERVA_SOC_POR_BATERIA`).
2. **Asignaciones inactivas** (`ASIGNACION_NO_ACTIVA`): no se copian. El ensayo dice cuántos
   administradores quedan sin ningún domo, y cuáles (línea "Administradores que quedan sin ningún
   domo").
3. **Alertas atendidas sin fecha** (`ALERTA_ATENDIDA_SIN_FECHA`): se copian como `cerrada`.
4. **Lecturas duplicadas en un bloque**: los valores instantáneos son los de la última lectura; la
   energía del intervalo (`energia_fv_wh`, `energia_consumo_wh`) es la suma de todas (ver "Bloques de
   10 s" en la sección 4). Pruebas en `tests/test_copiar_a_v2.py`, clase `Bloques`.
5. **Umbrales personalizados y globales**: se pierden. En v2 cada toma usa el preset de su categoría
   y del perfil del domo. El informe lista las tomas afectadas.
6. **`usuarios.desactivado_en`**: no existe en v2.
7. **Administradores sin domo**: en DB-2, `POST /auth/registro` exige `domo_id` y crea la asignación
   en la misma transacción.
8. **Corte**: el respaldo es una foto. Para el corte definitivo (DB-2): detener el listener, respaldar,
   copiar y arrancar contra v2. Lo que llegue a v1 después del respaldo no pasa a v2.

---

## 7. Ronda DB-2: el backend sobre v2

Hecho en la ronda DB-2 (ver la lista de la sección 5). Lo que conviene saber al usarlo:

- **La rama decide la base.** `backend/database.py` usa `DATABASE_URL_V2` y falla con un mensaje
  claro si no está. `DATABASE_URL` solo la leen `tools/respaldo_bd.py --origen v1` (el respaldo de v1
  para la copia) y `alembic_v2/env.py` (para negarse si son la misma base).
- **Acceso.** Todo sale de `asignaciones`: `routers/domos.py` tiene `_domos_accesibles`, `rol_en_domo`,
  `domo_accesible` (404) y `domo_del_dueno` (403 si lo ve como administrador). El `rol` del token y de
  `/users/me` se calcula: `dueño` si la cuenta es dueña de algún domo.
- **Umbrales y prioridad** se calculan en cada respuesta y en cada evaluación de R-13
  (`servicios/categorias.preset_de`). `DispositivoOut` los sigue trayendo.
- **Telemetría.** `servicios/mediciones.py` es el único lugar con el reparto de temas MQTT y de los
  POST de `/lecturas-*` en columnas, y con el upsert por bloque (energía sumada). Lo usan el listener,
  los routers de lecturas, R-13, el panel, los históricos, los reportes y el generador.
- **Nombres de v1 en el código.** `Alerta.tipo_alerta`, `descripcion`, `nivel_prioridad`,
  `estado_alerta`, `variable_asociada`, `valor_limite`, `Usuario.fecha_creacion` y
  `Domo.fecha_registro` son `synonym` de las columnas nuevas; `Usuario.rol`, `Usuario.estado_cuenta`,
  `Domo.propietario_id` y `Dispositivo.estado_conexion` son propiedades calculadas.

### Cambios de contrato de la API (inevitables)

| Endpoint | Cambio | Qué tocar en el frontend (DB-3) |
|---|---|---|
| `POST /auth/registro` | Exige `domo_id` (decisión 7): 422 sin él, 404 si el domo no es del dueño, 403 si solo lo administra | `Administradores.jsx`, `FormularioCrear`: elegir el domo y mandarlo. **Hasta entonces no se pueden crear administradores desde la app** |
| `PATCH /domos/{id}/administradores/{admin}` con `estado_asignacion: inactivo` | Borra la asignación. 409 si es el único domo de la cuenta (quedaría sin que nadie la vea) | Mostrar el mensaje del 409 (ya muestra `e.message`); las asignaciones quitadas ya no aparecen en la lista |
| `GET /administradores` | `cuenta_propia` = la cuenta solo tiene asignaciones de administrador en domos de este dueño (antes: la creó él) | Nada |
| `PATCH .../dispositivos/{id}` y `POST .../dispositivos` con umbrales | 422 con el motivo (ya no se editan). Un administrador sigue recibiendo 403 | `PanelDispositivo.jsx`: quitar el modo avanzado; `TarjetaDispositivo.jsx`: la marca "Personalizado" ya no aparece (`personalizado` siempre false) |
| `PUT /domos/{id}/configuracion` con `umbral_soc_bajo`, `umbral_soc_recuperacion` o los campos de texto libre | 422 con el motivo | Nada: `Configuracion.jsx` solo manda `bateria` y `perfil_control` |
| `PUT /domos/{id}/configuracion` | `tomas_recalculadas` = tomas no esenciales del domo si cambió batería o perfil; `tomas_personalizadas` = 0 | Opcional: simplificar el aviso |
| `POST /domos/{id}/alertas` | `tipo_alerta`, `nivel_prioridad` y `descripcion` obligatorios; acepta `id_fisico_ref`. `GET` acepta `?id_fisico_ref=` | Nada (el frontend no crea alertas) |
| `AlertaOut` | Campos nuevos `id_fisico_ref` y `creada_por_r13` | Nada |
| `POST /domos/{id}/lecturas-consumo` con `dispositivo_id` | 422 (v2 guarda el consumo del domo) | Nada |
| `POST /domos/{id}/lecturas-sensores` | `variable_medida` debe ser `frecuencia` o `factor_potencia` | Nada |
| `POST /domos/{id}/lecturas-*` | La lectura cae en el bloque de 10 s; dos POST en el mismo bloque devuelven el mismo `id` | Nada |
| `GET .../estado-comunicacion` | Solo el último estado (lista de 0 o 1); `senal`, `estado_mqtt` e `identificador_dispositivo` van null | Nada (el frontend no lo usa) |
| `DomoOut`, `UsuarioOut`, `AdministradorDomoOut` | `descripcion`, `estado_conexion`, `fotografia`, `permisos_asignados` y `fecha_asignacion` van null; `estado_domo` siempre `activo`. `PUT /users/me` ya no guarda `fotografia` | Nada |
| Un usuario dueño de un domo y administrador de otro | El menú lo trata como dueño en todos (`rol` de la cuenta); en el domo que administra, las acciones del dueño dan 403 | `Dispositivos.jsx` decide con `usuario.rol`: debería usar el rol en el domo (caso raro en el prototipo) |

### Pendiente para DB-3 (hecho: ver la sección 8)

- Frontend: los cambios de la tabla anterior (sobre todo `domo_id` al crear administradores).
- Esquema `bi` y `lector_bi` sobre v2 (vistas más simples: una sola tabla de mediciones), con
  `tests/test_bi_postgres.py` (hoy omitida en esta rama) y `tools/probar_lector_bi.py`.
- `docs/arquitectura_sistema.md`, `backend/README.md` y `docs/PowerBI.md` describiendo v2.
- `keepalive.py` (GitHub Actions) sigue haciendo ping solo a la base v1 (`DATABASE_URL`); si el proyecto
  v2 de Supabase es del plan gratuito, necesita su propio secreto.
- `tests/test_migracion_postgres.py` prueba la línea de migraciones v1: queda omitida en esta rama
  (corre en la etiqueta `v1-modelo-actual`); su parte de API sobre Postgres tiene el equivalente v2 en
  `tests/test_esquema_v2_postgres.py`.

---

## 8. Ronda DB-3: cierre para las mediciones del martes

- **Datos viejos no son actuales.** `servicios/mediciones.VIGENCIA_S = 120` (la misma ventana que la
  tarjeta de Autonomía). `GET /estado-general` devuelve `potencia_generada`, `potencia_consumida`,
  `estado_carga_soc` y `estado_bateria` en `null` si su último valor es más viejo; cada medida por
  separado. R-13 (automático y `/evaluar`) no decide sin un SoC actual o si la última lectura de
  batería llegó sin SoC: 409 con el motivo, y el listener lo registra una vez por domo
  (`servicios/control_energetico.soc_actual`). `/control-energetico/estado` solo anuncia
  `tomas_por_cortar` con un SoC actual. Era una **regresión de v2**: v1 tomaba como "última
  lectura" la fila más reciente aunque viniera en null (y entonces no evaluaba); v2 saltaba las
  filas sin dato y caía en el último valor de días atrás. Ninguna de las dos miraba la antigüedad
  (si el ESP32 dejaba de publicar, v1 también usaba el último SoC): eso es nuevo.
- **Frontend:** Crear administrador elige el domo (los domos donde el usuario es dueño) y manda
  `domo_id`; el modo avanzado de umbrales se quitó (umbrales calculados en solo lectura); el rol
  se decide por domo (`frontend/src/utils/roles.js`, con `DomoOut.propietario_id`) en Dispositivos,
  Configuración y la lista de domos de Administradores; el Panel muestra "Sin dato de los últimos
  2 minutos"; el menú lateral es más compacto en pantallas md+.
- **Power BI:** revisión `4e8b2c6d9f17` con las mismas 9 vistas y columnas que v1 (diferencias en
  `docs/PowerBI.md`, 7.0). Las 9 tienen equivalente; la única con otra semántica es
  `v_estado_actual` (último valor no nulo de cada medida, con su fecha).
- **Copia:** `REVISION_V2 = 4e8b2c6d9f17`; el ensayo muestra la energía total por domo en el
  respaldo y en v2.
- **Keepalive:** hace ping a `DATABASE_URL` y `DATABASE_URL_V2` (secreto nuevo de GitHub con ese
  nombre). Los workflows programados corren desde la rama por defecto (`main`): hasta que esto
  llegue a `main`, el ping a v2 no corre solo.
- **Cambio y vuelta atrás:** `docs/modelo_v2/CAMBIO_DEFINITIVO.md`.

### Lo que v1 ya hacía igual (no es regresión)

- **Volver a cortar una toma encendida a mano.** R-13 solo protege de la *reactivación* automática
  a una toma que alguien apagó a mano. Si alguien enciende a mano una toma que está bajo su umbral,
  la siguiente evaluación (≤ 30 s con el listener) la vuelve a cortar. En v1 era igual: no hay
  ninguna regla de "primacía de la decisión humana" en el código ni en las pruebas. **Resuelto en
  DB-4 (sección 9).**
- **"Supervivencia" con solo una toma confort cortada.** La regla es la de v1: `ahorro` solo si
  TODAS las tomas cortadas por R-13 son prescindibles; cualquier otra (confort o importante) es
  `supervivencia`. El asistente preselecciona `confort` al crear una toma (también en v1): si
  `toma2` se creó sin cambiar la categoría, es confort. Además, una toma cortada sigue contando
  hasta que se reactiva o alguien la toca: con el SoC viejo R-13 ya no la reactiva.
- **Históricos y Reportes dan números distintos de energía** (sección 8.1).

### 8.1 Energía: Históricos frente a Reportes

- **Históricos** (`GET /historico`) suma `energia_fv_wh` / `energia_consumo_wh` del rango, tal
  como llegaron (v1: `SUM(energia_generada)` y `SUM(energia_consumida)`, la misma fórmula).
- **Reportes** (`GET /reportes/resumen`) integra la potencia con trapecios entre lecturas seguidas
  y no integra los huecos de más de 10 minutos (v1: igual; DB-2 solo cambió de qué tabla lee).
- Antes del 2026-10-27 (commit `cd8aba3`) el simulador escribía en `energia_*` la energía del
  intervalo en **tiempo simulado**, acelerado para ver un día en minutos: cada lectura de 5 s
  llevaba la energía de muchos minutos simulados. Esa energía no corresponde a ninguna potencia
  medida durante ese tiempo real. La suma de Históricos (120,3 / 83,9 kWh) está dominada por esos
  datos; la integración de Reportes (0,9 / 0,6 kWh) solo cuenta el tiempo real con lecturas.
  Ninguna de las dos fórmulas cambió en v2.
- **120,8 kWh en el respaldo frente a 120,3 en Históricos.** La copia no puede perder energía
  sin dejarlo en el ensayo: suma la de cada bloque y solo descarta un valor fuera de rango (que
  sale como `MEDIDA_FUERA_DE_RANGO`) o una lectura sin fecha (`LECTURA_SIN_FECHA`). Desde DB-3 el
  ensayo imprime el total por domo en el respaldo y en v2. Lo más probable es el **rango**:
  Históricos filtra `fecha_desde`–`fecha_hasta` (con un atajo de 30 días, desde ahora menos 30
  días, en UTC), mientras que la suma del respaldo cubre todas sus filas. Con energía inflada por
  el simulador acelerado, unos pocos minutos fuera del rango bastan para 0,5 kWh de generación, y
  el consumo del mismo tramo (30–120 W frente a ~500 W de generación) puede quedar por debajo de
  la décima. Ver los comandos de comprobación en
  `CAMBIO_DEFINITIVO.md`.

---

## 9. Ronda DB-4: primacía de la decisión humana y selectores del bombillo

**Sin cambios de esquema** (ninguna migración, ni v1 ni v2): todo sale de las tablas actuales
(lineamiento L3).

### 9.1 Primacía de la decisión humana en R-13 (regla A de David)

- **Regla.** Si la ÚLTIMA acción sobre una toma es un encendido hecho por una persona
  (`acciones.actor_tipo` `dueno` o `administrador`), R-13 no la corta por su umbral de apagado
  hasta que la batería se recupere: hasta que, DESPUÉS de esa acción, haya una medición del domo
  con `soc_pct` >= el umbral de reactivación de la toma (calculado con su categoría, la batería y
  el perfil del domo). Desde ese momento vuelve a la regla normal.
- **Excepción de la reserva.** Si el SoC actual está bajo la reserva de la batería del domo
  (`reserva_de`: lifepo4 20, agm 50; estrictamente menor), R-13 la corta igual. Esa acción guarda
  `condicion = "modo=supervivencia; soc=<SoC>; motivo=reserva"` y en `/evaluar` sale con
  `motivo: "reserva"` y `umbral_aplicado` = la reserva. Si es confort o importante, crea la alerta
  `bateria_baja` como cualquier corte (será `critico`, porque el SoC está bajo la reserva).
- **Lo que no cambia.** Apagar a mano sigue igual (R-13 no reactiva lo que apagó una persona); una
  toma reactivada por R-13 no tiene primacía; sin SoC actual no se decide (DB-3); solo tomas
  activas, nunca bombillos ni esenciales; publicar después del commit, sin `retain`; 30 s por
  domo; orden determinista; solo cierra sus alertas. Igual en tomas reales y simuladas.
- **Cómo se calcula** (`servicios/control_energetico.primacias_humanas`):
  1. `_ultimas_acciones`: la última acción de cada toma activa del domo, en una consulta que
     parte de `dispositivos` y busca, por toma, la última fila con el índice
     `acciones_dispositivo_fecha_idx (dispositivo_id, fecha_hora DESC, id DESC)`. Reemplaza las N
     consultas (una por toma) que hacía `_tomas_apagadas_por_sistema`, así que la evaluación hace
     menos viajes que en DB-3.
  2. Si ninguna toma tiene como última acción un encendido humano (lo normal), no consulta nada
     más. Si alguna lo tiene, UNA consulta para todo el domo:
     `SELECT EXISTS (SELECT * FROM mediciones WHERE domo_id = :d AND fecha_hora > :accion_1 AND soc_pct >= :reactivacion_1) AS t0, EXISTS (…) AS t1, …`,
     un `EXISTS` por toma sobre el índice único `(domo_id, fecha_hora)`. Cada uno para en la
     primera fila que cumple.
  3. `decidir_r13` sigue siendo pura: recibe los id con primacía vigente
     (`con_primacia_humana`) y devuelve además `respetadas` y `cortadas_por_reserva`.
- **Dónde se ve.**
  - `POST /evaluar`: `respetadas_por_primacia_humana` (nombre, umbral de apagado, `hasta_soc`,
    `reserva_soc`, `encendida_desde`) y `motivo` en cada acción.
  - Listener: una línea INFO por toma y por encendido mientras dure ("respeta 'X', encendida a
    mano…"), y "motivo=reserva" en la línea del corte.
  - `GET .../estado`: `tomas_por_cortar` ya no anuncia las que se van a respetar (sí las que están
    bajo la reserva).
  - `DispositivoOut`: `primacia_humana_hasta_soc` y `primacia_humana_desde` (null sin primacía).
    La app muestra en la tarjeta y el panel "Encendida a mano: el sistema la respeta hasta que la
    batería llegue a X % (la corta si baja de la reserva, Y %)", y el aviso antes de encender bajo
    el umbral explica lo mismo (o que la volverá a apagar si la batería está bajo la reserva).
  - Historial: la condición con `motivo=reserva`.
- **Generador del domo de demostración.** Aplica la misma regla con su reloj simulado
  (`TomaSim.primacia`); `tests/test_demo.py` comprueba que decide lo mismo que el backend,
  incluido el motivo.
- **Detalle de fechas.** `mediciones.fecha_hora` es el inicio del bloque de 10 s: la medición del
  mismo bloque que la acción no cuenta como "después" (la primacía puede durar hasta 10 s más; es
  lo conservador).

### 9.2 Color y temperatura del bombillo

- **Recorrido.** `color` y `temperatura_color` son texto en `dispositivos`; entran por
  `DispositivoCreate`/`DispositivoUpdate` y salen en `DispositivoOut`. El backend NO los publica
  por MQTT (`routers/dispositivos.py` solo publica `estado` y `brillo`), y el firmware
  (`armarDps` en `firmware/domo_nodo/domo_nodo.ino`) solo escribe en el bombillo Tuya el DPS 20
  (encendido) y el 22 (brillo, 10–1000), siempre con el 21 (`work_mode`) en `"white"`. No usa el
  23 (temperatura) ni el 24 (color). **Conclusión: el color y la temperatura solo se guardan; el
  brillo sí llega al bombillo real** (y al cambiarlo, el bombillo pasa a luz blanca).
- **Lista.** No había ninguna en el backend ni en el firmware. Se definió una corta en
  `backend/servicios/iluminacion.py` (única fuente): colores `blanco`, `amarillo`, `naranja`,
  `rojo`, `rosa`, `morado`, `azul`, `verde`; temperaturas `calida`, `neutra`, `fria`. Pydantic
  la valida (se aceptan mayúsculas y espacios alrededor, y se guarda en minúsculas) y responde 422
  "Color: Debe ser uno de: …". `GET /iluminacion/opciones` (con sesión, solo lectura) la entrega
  con sus nombres y `se_envian_al_bombillo: false`.
- **Datos viejos.** `DispositivoOut` no valida, así que un valor como `"string"` se sigue leyendo;
  el selector lo muestra como "Sin definir", y la app no lo borra si solo se cambia el brillo. No
  se tocó `tools/copiar_a_v2.py` ni la base. (`"rojo"`, el otro valor copiado, ya es válido.)
- **Pendiente (después del martes):** mandar color y temperatura al bombillo, y cambiar el brillo
  sin perder el color. Los dos tocan el firmware.
