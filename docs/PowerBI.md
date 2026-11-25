# Vatia en Power BI

Cómo llevar los datos de Vatia a Power BI para el documento de grado: con CSV exportados (secciones
1 a 6) o conectado a la base, casi en tiempo real (sección 7).

> **Sobre los datos.** Hasta ahora, la mayor parte de las lecturas viene del simulador
> (`backend/tools/simulador_telemetria.py`), no de sensores instalados. Un informe con estos
> datos demuestra el funcionamiento del sistema, pero no mide el desempeño energético real
> del domo. Dilo así en cualquier figura o conclusión que los use, y separa por fechas lo
> simulado de lo medido cuando existan datos reales. Esa distinción no está en los datos.

## 1. Exportar

`backend/tools/exportar_bi.py` es de solo lectura: abre la transacción como `READ ONLY`, usa
la `DATABASE_URL` de `backend/.env` sin imprimirla y nunca lee las cuentas de usuario.

Desde `backend/`, con el entorno virtual activo:

```
python tools/exportar_bi.py
python tools/exportar_bi.py --domo 1 --desde 2026-11-01 --hasta 2026-11-30
```

| Parámetro | Por defecto | Uso |
|---|---|---|
| `--domo` | Todos | Domo a exportar; se puede repetir |
| `--desde`, `--hasta` | Todo lo que haya | Fechas `AAAA-MM-DD` de Bogotá, ambas incluidas |

Cada corrida crea su propia carpeta `%USERPROFILE%\vatia_bi\AAAAMMDD_HHMM`, fuera del
repositorio y de OneDrive. Los archivos están en UTF-8 con BOM, separados por coma y con
punto decimal. Las horas son de Bogotá.

Dos reglas vienen de la aplicación. Un hueco de más de 10 minutos entre lecturas no se integra
en la energía, y la categoría de cada dispositivo es la actual, porque el historial no guarda
la que tenía en cada momento.

### Domo de demostración

Un domo con `es_demostracion` en 1 (en `dim_domo.csv`, con `origen_datos` "Demostración · datos
sintéticos") tiene SOLO datos sintéticos, generados por `backend/tools/generar_datos_demo.py`:
lecturas, acciones, cortes y alertas. Sirve para armar y mostrar el informe, nunca como resultado
del sistema.

- Si exportas todos los domos, filtra por `dim_domo[es_demostracion]` o pon `origen_datos` como
  segmentación, para no mezclar datos sintéticos con los del domo del proyecto.
- En cualquier figura con el domo de demostración escribe "Demostración · datos sintéticos". Con
  `--domo <id>` exportas solo ese domo.
- `origen_datos` "Domo del proyecto" no quiere decir medido: ver la nota del principio sobre el
  simulador.

## 2. Modelo

| Archivo | Tipo | Grano | Columnas principales |
|---|---|---|---|
| `hechos_lecturas_5min.csv` | Hecho | Domo y 5 minutos | `generacion_w`, `consumo_w`, `soc` (promedios), `generacion_wh`, `consumo_wh`, `minutos_bajo_reserva` |
| `hechos_acciones.csv` | Hecho | Una acción | `rol`, `accion`, `automatica` (1 si la hizo el sistema), `resultado` |
| `hechos_alertas.csv` | Hecho | Una alerta | `tipo_alerta`, `nivel_prioridad`, `estado_alerta`, `valor_detectado`, `valor_limite`, `minutos_hasta_atendida` |
| `hechos_cortes.csv` | Hecho | Un corte de R-13 | `inicio_local`, `fin_local`, `horas_apagada`, `empieza_en_el_rango`. Termina en la siguiente acción sobre la toma o cuando se desactivó |
| `dim_fecha.csv` | Dimensión | Un día | `fecha`, `anio`, `mes`, `nombre_mes`, `dia_semana` |
| `dim_hora.csv` | Dimensión | Una hora | `etiqueta`, `franja` (Madrugada, Mañana, Tarde, Noche) |
| `dim_dispositivo.csv` | Dimensión | Un dispositivo | `nombre`, `tipo`, `categoria`, `ubicacion`, `activo`, `desactivado_en_local`, `real`, umbrales |
| `dim_domo.csv` | Dimensión | Un domo | `nombre`, `tipo_bateria`, `capacidad_wh`, `reserva_soc`, `perfil_control`, `es_demostracion`, `origen_datos` |

## 3. Importar

1. En Power BI Desktop: **Obtener datos → Texto/CSV**, y carga los ocho archivos.
2. Por cada uno, elige **Transformar datos** y confirma que el origen sea **65001: Unicode
   (UTF-8)** y el delimitador **Coma**.
3. Los decimales usan punto. Si Power BI está en español, convierte esas columnas con **clic
   derecho → Cambiar tipo → Usar configuración regional → Número decimal → Inglés (Estados
   Unidos)**. Si no, `12.5` puede leerse como 125.
4. Cambia `fecha` de `dim_fecha` a tipo **Fecha** y márcala con **Marcar como tabla de fechas**.
5. Para actualizar con una exportación nueva, cambia la carpeta en **Configuración de origen de
   datos**, o usa un parámetro con la ruta.

## 4. Relaciones

Todas de uno a varios, con dirección de filtro única desde la dimensión hacia el hecho:

| Desde (uno) | Hacia (varios) |
|---|---|
| `dim_fecha[fecha_id]` | `hechos_lecturas_5min[fecha_id]`, `hechos_acciones[fecha_id]`, `hechos_alertas[fecha_id]`, `hechos_cortes[fecha_id]` |
| `dim_hora[hora]` | `hechos_lecturas_5min[hora]`, `hechos_acciones[hora]`, `hechos_alertas[hora]`, `hechos_cortes[hora]` |
| `dim_domo[domo_id]` | Los cuatro hechos y `dim_dispositivo[domo_id]` |
| `dim_dispositivo[dispositivo_id]` | `hechos_acciones[dispositivo_id]`, `hechos_cortes[dispositivo_id]` |

`dim_domo` y `dim_dispositivo` llegan ambas a `hechos_acciones` y a `hechos_cortes`. Si Power BI desactiva una
relación por ambigüedad, deja activa la de `dim_dispositivo` y filtra por domo a través de ella.

## 5. Medidas DAX sugeridas

```dax
kWh generados = SUM ( hechos_lecturas_5min[generacion_wh] ) / 1000

kWh consumidos = SUM ( hechos_lecturas_5min[consumo_wh] ) / 1000

Balance kWh = [kWh generados] - [kWh consumidos]

Horas bajo reserva = SUM ( hechos_lecturas_5min[minutos_bajo_reserva] ) / 60

SoC promedio = AVERAGE ( hechos_lecturas_5min[soc] )

SoC mínimo = MIN ( hechos_lecturas_5min[soc] )

Cortes del sistema =
CALCULATE (
    COUNTROWS ( hechos_acciones ),
    hechos_acciones[automatica] = 1,
    hechos_acciones[accion] = "apagar"
)

% acciones automáticas =
DIVIDE (
    CALCULATE ( COUNTROWS ( hechos_acciones ), hechos_acciones[automatica] = 1 ),
    COUNTROWS ( hechos_acciones )
)

Minutos medios hasta atender = AVERAGE ( hechos_alertas[minutos_hasta_atendida] )
```

Para los cortes por categoría, usa `Cortes del sistema` con `dim_dispositivo[categoria]` como eje.

```dax
Horas apagada por el sistema = SUM ( hechos_cortes[horas_apagada] )
```

Con un rango, un corte que empezó antes aparece recortado y con `empieza_en_el_rango` en 0: suma
sus horas, pero no lo cuentes como corte nuevo.

El SoC mínimo sale de promedios de 5 minutos, así que puede ser un poco mayor que el de la página
de Reportes, que usa cada lectura.

`Minutos medios hasta atender` solo cuenta las alertas atendidas después de la migración
`d5b1f3a9c2e7`: antes no se guardaba ese instante.

## 6. Visuales sugeridos

| Visual | Campos | Qué muestra |
|---|---|---|
| Columnas agrupadas | Eje `dim_fecha[fecha]`; valores `kWh generados` y `kWh consumidos` | Si la generación diaria cubre el consumo |
| Líneas | Eje `dim_hora[etiqueta]`; valores promedio de `generacion_w` y `consumo_w` | Perfil horario: el desfase entre el sol y el uso |
| Líneas con línea constante | Eje `dim_fecha[fecha]`; valores `SoC mínimo` y `SoC promedio`; línea en `dim_domo[reserva_soc]` | Cuánto se acerca la batería a la reserva |
| Barras | Eje `dim_dispositivo[categoria]`; valor `Cortes del sistema` | Que R-13 corta primero lo prescindible |
| Tarjetas | `kWh generados`, `Horas bajo reserva`, `% acciones automáticas`, `Minutos medios hasta atender` | Resumen del período |

Usa siempre el mismo color por serie, una sola escala por gráfica, y escribe en cada figura el
período, el domo y si los datos son simulados.

## 7. En vivo con DirectQuery

Las secciones 1 a 6 trabajan con CSV exportados. Esta conecta Power BI directo a la base, solo
leyendo, para una página que se actualiza sola cada pocos segundos. Usa un **modelo mixto**:

- **En vivo** (DirectQuery): cada actualización consulta la base. Pocas filas, pocas visualizaciones.
- **Histórico** (Import): Power BI guarda una copia y la refresca cuando tú lo pides.

### 7.0 Modelo v2 (rama `feature/modelo-simplificado`)

En el modelo v2 el esquema `bi` lo crea la revisión **`4e8b2c6d9f17`** de `alembic_v2`, con las
**mismas 9 vistas y los mismos nombres de columna** que v1: el informe de Power BI no se rehace,
solo se cambia el servidor. El resto de esta sección vale igual, con estas diferencias:

- **Aplicarla:** desde `backend/`, `alembic -c alembic_v2.ini upgrade head` (no
  `alembic upgrade head`, que es la línea v1). No hace falta la consulta previa de 7.2: el
  proyecto v2 es nuevo. El procedimiento completo del cambio está en
  `docs/modelo_v2/CAMBIO_DEFINITIVO.md`.
- **Otro `lector_bi`:** los roles son de cada proyecto de Supabase. El de v2 nace SIN contraseña
  y SIN login, como en v1: dale los suyos con 7.3 en el SQL Editor **del proyecto v2** (otra
  contraseña, guardada en tu gestor) y borra el snippet. Usuario del pooler:
  `lector_bi.<referencia-del-proyecto-v2>`. Si se corre `alembic -c alembic_v2.ini downgrade base`,
  el rol se borra y hay que repetir 7.3 después del `upgrade head`.
- **Cambiar el informe de servidor:** en Power BI Desktop, **Transformar datos → Configuración de
  origen de datos → Cambiar origen**, pon el host del Session pooler del proyecto v2 y, en
  **Editar permisos → Credenciales**, el usuario y la contraseña del `lector_bi` de v2.
- **Probarlo:** `python tools/probar_lector_bi.py`, escribiendo la referencia del proyecto v2
  cuando la pida.
- **Qué cambia por debajo** (las columnas no):
  - `v_estado_actual`: el **último valor no nulo** de cada medida con su fecha (v1 tomaba la última
    fila aunque viniera en null). Un ESP32 sin sensor no borra el último SoC: mira
    `fecha_soc_local` y `minutos_desde_ultima_lectura` antes de leerlo como actual. La app, en
    cambio, no muestra como actual nada de más de 2 minutos.
  - `v_dispositivos`: `prioridad`, `umbral_apagado_soc` y `umbral_reactivacion_soc` se
    **calculan** con la categoría y la batería y el perfil del domo (en v2 no se guardan). Son
    `numeric` (en v1, `real`): si Power BI se queja del tipo, **Actualizar** la tabla.
  - `v_acciones`: `rol` sigue diciendo `dueño`, `administrador` o `sistema`.
  - `v_lecturas_*` y `v_pzem` salen de `mediciones` (una fila por domo y bloque de 10 s): mismas
    reglas de energía (trapecios, huecos de más de 10 min sin integrar).
  - No hay función `bi.json_o_vacio`: la batería y el perfil son columnas de `domos`.
- Las pruebas de las vistas contra Postgres están en `backend/tests/test_esquema_v2_postgres.py`
  (clase `BiSobrePostgres`) y las de las constantes en `backend/tests/test_bi_v2.py`.

### 7.1 Qué hay en la base

La migración `a9e4c1f7b3d2` crea el esquema `bi` con vistas de solo lectura y el rol `lector_bi`.
Fechas y horas en hora de Bogotá. Ninguna vista lee las cuentas (ni correos, ni nombres de usuario,
ni hashes; en las acciones solo va el rol), ni `id_fisico`.

| Vista | Modo | Grano | Columnas principales |
|---|---|---|---|
| `bi.v_domos` | Dual | Un domo | `nombre`, `es_demostracion`, `origen_datos`, `tipo_bateria`, `capacidad_wh`, `reserva_soc`, `perfil_control` |
| `bi.v_estado_actual` | DirectQuery | Un domo | `soc`, `generacion_w`, `consumo_w` (la última lectura de cada una, con su fecha), `ultima_lectura_local`, `minutos_desde_ultima_lectura`, `estado_energetico` (normal, ahorro o supervivencia, como la app), `tomas_cortadas` |
| `bi.v_lecturas_minuto` | DirectQuery | Domo y minuto, últimas 48 h | `minuto_local`, `generacion_w`, `consumo_w`, `soc` (promedios) |
| `bi.v_pzem` | DirectQuery | Domo y minuto | `frecuencia_hz`, `factor_potencia` |
| `bi.v_lecturas_5min` | Import | Domo y 5 minutos, todo el histórico | Las mismas de `hechos_lecturas_5min.csv` |
| `bi.v_dispositivos` | Import | Un dispositivo | Las de `dim_dispositivo.csv`, más `estado` y `prioridad` |
| `bi.v_acciones` | Import | Una acción | `rol`, `accion`, `automatica` (verdadero o falso), `resultado` |
| `bi.v_alertas` | Import | Una alerta | Las de `hechos_alertas.csv`, más `variable_asociada` y `atendida_local` |
| `bi.v_cortes` | Import | Un corte de R-13 | `categoria`, `inicio_local`, `fin_local`, `horas_apagada`, `vigente` |

Todas traen `fecha` y `hora` locales para relacionarlas con un calendario. Las reglas de energía,
cortes y modo son las de la app (`servicios/reportes.py` y `servicios/control_energetico.py`),
escritas en SQL porque una vista no puede llamar a Python; `backend/tests/test_bi_postgres.py`
compara cada vista con esas funciones.

Diferencias con los CSV y con la página de Reportes:

- `v_cortes` no recorta: un corte que empezó ayer y sigue hoy suma todas sus horas en el día en que
  empezó. Los CSV con `--desde` lo recortan al rango.
- `v_lecturas_5min` integra todo el histórico. Al filtrar un rango en Power BI, el primer tramo del
  primer día sí cuenta (Reportes no lo cuenta): una diferencia de una sola lectura.
- `v_pzem` solo tiene datos del ESP32 real: el simulador no publica frecuencia ni factor de potencia.

### 7.2 Aplicar la migración

1. En el **SQL Editor** de Supabase corre la consulta de solo lectura
   `backend/tests/consulta_previa_a9e4c1f7b3d2.sql`. `listo_para_migrar` debe salir `true`.
   `con_indices_existentes_de_lecturas` debe salir `true`: las vistas en vivo se apoyan en los
   índices `(domo_id, fecha_hora)` que ya existen en Supabase (`idx_lecturas_*_domo_fecha`).
2. Desde `backend/`, con el entorno virtual activo: `alembic upgrade head`.

La migración solo crea dos índices: `historial_acciones (dispositivo_id, fecha_hora, id)`, para la
última acción de cada toma, y uno parcial en `lecturas_sensores` para el PZEM por minuto. Mientras
se construyen bloquean por unos segundos las escrituras en esas dos tablas: el listener espera y
no pierde datos, pero mejor hacerlo sin el ESP32 publicando.

### 7.3 Darle LOGIN y contraseña a `lector_bi`

La migración lo crea sin poder iniciar sesión. Genera una contraseña larga en tu PC:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Guárdala en tu gestor de contraseñas, nunca en el repositorio ni en `backend/.env`. En el
**SQL Editor**, en un snippet nuevo:

```sql
ALTER ROLE lector_bi WITH LOGIN PASSWORD '<CONTRASEÑA_GENERADA>';
```

El SQL Editor guarda los snippets: bórralo después de correrlo. Para comprobar:

```sql
SELECT rolname, rolcanlogin, rolconfig FROM pg_roles WHERE rolname = 'lector_bi';
```

Debe salir `rolcanlogin = true` y en `rolconfig` `default_transaction_read_only=on` y
`statement_timeout=30s`: sus transacciones son de solo lectura por defecto y una consulta que pase
de 30 s se cancela. Lo que de verdad le impide leer o escribir otra cosa son los permisos: solo
tiene `USAGE` en `bi` y `SELECT` en las nueve vistas.

Cambiar la contraseña: `ALTER ROLE lector_bi WITH PASSWORD '<NUEVA>';`. Quitarle el acceso:
`ALTER ROLE lector_bi WITH NOLOGIN PASSWORD NULL;`.

### 7.4 Seguridad: RLS, la API de Supabase y los asesores

- Las tablas de `public` tienen RLS sin políticas. Las vistas son de `postgres` (dueño de las tablas
  y con `BYPASSRLS`) y no llevan `security_invoker`, así que leen las tablas con los permisos de su
  dueño. Por eso `lector_bi` no necesita, ni tiene, ningún permiso sobre `public`.
- No le pongas `security_invoker = on` a estas vistas: las consultas pasarían a hacerse como
  `lector_bi`, sin permiso sobre las tablas, y con RLS sin políticas no verían nada.
- El esquema `bi` no debe estar en **Project Settings → Data API → Exposed schemas** (déjalo en
  `public` y `graphql_public`). Además, `anon`, `authenticated`, `service_role` y `PUBLIC` no tienen
  ningún permiso en `bi`: la migración se los quita aunque algún privilegio por defecto se los
  hubiera dado. Aunque alguien expusiera el esquema, la API no podría leerlo.
- El asesor de seguridad **Security Definer View** marca las vistas sin `security_invoker` que la
  API puede leer. Estas no deberían salir, porque `bi` no está expuesto y los roles de la API no
  tienen permisos. Si alguna vez sale, es aceptable por lo anterior: solo `lector_bi` las lee, por
  una conexión directa con usuario y contraseña, y solo ve las columnas de las vistas.
- La función `bi.json_o_vacio` (lee `parametros_energeticos` sin fallar si el JSON está mal) tiene
  `search_path` fijo, así que el asesor **Function Search Path Mutable** no la marca.

### 7.5 Conectar Power BI Desktop

Datos de conexión (en Supabase: botón **Connect** → **Session pooler**):

| Campo | Valor |
|---|---|
| Servidor | El host del **Session pooler** con el puerto: `aws-…-<región>.pooler.supabase.com:5432` |
| Base de datos | `postgres` |
| Usuario | `lector_bi.<referencia-del-proyecto>`: el mismo que muestra Supabase como `postgres.<referencia>`, cambiando `postgres` por `lector_bi` |
| Contraseña | La de 7.3 |

Por qué el **Session pooler**: la conexión directa (`db.<referencia>.supabase.co`) es solo IPv6 si
no tienes el complemento de IPv4, y muchas redes de casa no tienen IPv6. El **Transaction pooler**
(puerto 6543) no conserva la sesión entre consultas y da errores con el conector de Power BI.

1. **Obtener datos → Base de datos PostgreSQL**.
2. Servidor y base de datos como en la tabla. **Modo de conectividad: DirectQuery**. No escribas
   una instrucción SQL.
3. Credenciales: pestaña **Base de datos**, usuario y contraseña. Deja marcada la opción de cifrar
   la conexión (SSL). Si Power BI dice que el certificado no es de confianza, descarga el
   certificado de **Project Settings → Database → SSL Configuration** e instálalo en Windows en
   **Entidades de certificación raíz de confianza** del usuario actual; no desactives el cifrado.
4. En el navegador, abre el esquema `bi` y marca `v_domos`, `v_estado_actual`, `v_lecturas_minuto`
   y `v_pzem`. **Cargar**.
5. Para Histórico, otra vez **Obtener datos → Base de datos PostgreSQL** con el mismo servidor, ahora
   en **Importar**: `v_lecturas_5min`, `v_dispositivos`, `v_acciones`, `v_alertas` y `v_cortes`.
   Power BI avisa que el modelo pasa a ser compuesto: acepta.
6. En la vista de **Modelo**, selecciona `v_domos` → **Propiedades → Avanzado → Modo de
   almacenamiento → Dual**: así filtra tanto las tablas en vivo como las importadas.

Si Power BI pide instalar Npgsql, actualiza Power BI Desktop: las versiones recientes ya lo traen.

Relaciones (uno a varios, filtro único desde la dimensión):

| Desde (uno) | Hacia (varios) |
|---|---|
| `v_domos[domo_id]` | `v_estado_actual`, `v_lecturas_minuto`, `v_pzem`, `v_lecturas_5min`, `v_dispositivos`, `v_alertas` (todas por `domo_id`) |
| `v_dispositivos[dispositivo_id]` | `v_acciones[dispositivo_id]`, `v_cortes[dispositivo_id]` |

Para Histórico crea un calendario en DAX y relaciónalo con la columna `fecha` de las tablas
importadas, no con las de En vivo:

```dax
Calendario = CALENDAR ( MIN ( v_lecturas_5min[fecha] ), MAX ( v_lecturas_5min[fecha] ) )
```

### 7.6 Actualización automática de la página En vivo

1. Selecciona la página En vivo, sin ninguna visualización seleccionada.
2. Panel **Formato** → **Actualización de página** → **Activado**.
3. Tipo **Intervalo fijo**, entre **10 y 30 segundos**. El ESP32 publica cada 10 s y el simulador
   cada 5 s: menos de 10 s no muestra nada nuevo y solo carga la base.

Cada visualización es una consulta en cada actualización: deja la página en cinco o seis. Usa en
ella solo tablas DirectQuery o Dual; las importadas no cambian al actualizar la página.

| Dónde | Intervalo mínimo |
|---|---|
| Power BI Desktop | Sin restricción |
| Servicio de Power BI con licencia Pro | 30 minutos |
| Servicio con capacidad Premium o Fabric | El que fije el administrador de la capacidad |

Para la demostración en vivo, usa Power BI Desktop. Publicado con Pro, la página En vivo se
actualiza como mucho cada 30 minutos.

### 7.7 Probarlo esta noche

En vivo, con el simulador en el domo 2 (que no debe tener un ESP32 publicando; el simulador lo
comprueba y se niega si lo tiene):

1. Aplica la migración y da LOGIN a `lector_bi` (7.2 y 7.3).
2. Arranca el broker (`docker compose up -d` en la raíz) y, en `backend/`, `python mqtt_listener.py`.
3. En otra terminal de `backend/`:
   `python tools/simulador_telemetria.py --domo 2 --intervalo 5 --ciclo 900`.
   Un día simulado dura 15 minutos, así que la generación y el SoC cambian a la vista. Con
   `--escenario demo-r13` ves además a R-13 cortar y reactivar tomas.
4. Abre Power BI Desktop, conecta (7.5), arma la página En vivo (7.8) y activa la actualización
   cada 10 s. Filtra por el domo 2.
5. Comprueba: `minutos_desde_ultima_lectura` se queda por debajo de 0,2; el SoC y la gráfica de la
   última hora se mueven; con `demo-r13`, `estado_energetico` pasa de `normal` a `ahorro` o
   `supervivencia` y `tomas_cortadas` sube.
6. Detén el simulador con `Ctrl+C`: en dos o tres minutos la tarjeta de comunicación debe pasar a
   "Sin datos recientes".

Las lecturas del simulador en el domo 2 quedan en la base como si fueran del domo del proyecto
(`origen_datos` "Domo del proyecto"): ver la nota del principio.

Histórico, con el domo de demostración:

1. Si todavía no existe, créalo (ver `backend/README.md`):
   `python tools/generar_datos_demo.py --dueno-correo <correo del dueño>`.
2. En Power BI, **Inicio → Actualizar** para traer las tablas importadas.
3. Pon una segmentación con `v_domos[origen_datos]` y elige "Demostración · datos sintéticos".
   Escribe esa marca en cada figura.

### 7.8 Visuales sugeridos

Medidas para En vivo (con un solo domo elegido en la segmentación):

```dax
SoC actual = SELECTEDVALUE ( v_estado_actual[soc] )

Reserva = SELECTEDVALUE ( v_estado_actual[reserva_soc] )

Generación W = SELECTEDVALUE ( v_estado_actual[generacion_w] )

Consumo W = SELECTEDVALUE ( v_estado_actual[consumo_w] )

Balance W = [Generación W] - [Consumo W]

Estado energético = SELECTEDVALUE ( v_estado_actual[estado_energetico] )

Tomas cortadas = SELECTEDVALUE ( v_estado_actual[tomas_cortadas] )

Minutos sin datos = SELECTEDVALUE ( v_estado_actual[minutos_desde_ultima_lectura] )

Comunicación =
SWITCH (
    TRUE (),
    ISBLANK ( [Minutos sin datos] ), "Sin lecturas",
    [Minutos sin datos] > 2, "Sin datos recientes",
    "En línea"
)

Frecuencia Hz =
VAR ultimo = MAX ( v_pzem[minuto_local] )
RETURN CALCULATE ( AVERAGE ( v_pzem[frecuencia_hz] ), v_pzem[minuto_local] = ultimo )

Factor de potencia =
VAR ultimo = MAX ( v_pzem[minuto_local] )
RETURN CALCULATE ( AVERAGE ( v_pzem[factor_potencia] ), v_pzem[minuto_local] = ultimo )
```

| Visual (En vivo) | Campos | Qué muestra |
|---|---|---|
| Medidor | Valor `SoC actual`, mínimo 0, máximo 100, destino `Reserva` | Cuánto le queda a la batería sobre la reserva |
| Tarjeta de varias filas | `Generación W`, `Consumo W`, `Balance W` | Si en este momento se carga o se descarga |
| Tarjeta de varias filas | `Estado energético`, `Tomas cortadas` | Qué está haciendo R-13 |
| Tarjeta | `Comunicación` y `Minutos sin datos` | Si los datos están al día |
| Líneas | Eje `v_lecturas_minuto[minuto_local]`; valores `generacion_w` y `consumo_w`; filtro de **tiempo relativo** en los últimos 120 minutos | Las últimas dos horas de un vistazo |
| Líneas | Eje `v_lecturas_minuto[minuto_local]`; valor `soc`; línea constante en `Reserva`; mismo filtro | La tendencia del SoC |

`Frecuencia Hz` y `Factor de potencia` caben como séptima tarjeta solo con el ESP32 real. El filtro
de tiempo relativo usa la hora del PC: con el PC en hora de Colombia coincide con `minuto_local`.

Para Histórico sirven las medidas de la sección 5, cambiando las tablas:

```dax
kWh generados = SUM ( v_lecturas_5min[generacion_wh] ) / 1000

kWh consumidos = SUM ( v_lecturas_5min[consumo_wh] ) / 1000

Balance kWh = [kWh generados] - [kWh consumidos]

Horas bajo reserva = SUM ( v_lecturas_5min[minutos_bajo_reserva] ) / 60

SoC promedio = AVERAGE ( v_lecturas_5min[soc] )

SoC mínimo = MIN ( v_lecturas_5min[soc] )

Cortes del sistema = COUNTROWS ( v_cortes )

Horas apagada por el sistema = SUM ( v_cortes[horas_apagada] )

% acciones automáticas =
DIVIDE (
    CALCULATE ( COUNTROWS ( v_acciones ), v_acciones[automatica] = TRUE () ),
    COUNTROWS ( v_acciones )
)

Minutos medios hasta atender = AVERAGE ( v_alertas[minutos_hasta_atendida] )
```

| Visual (Histórico) | Campos | Qué muestra |
|---|---|---|
| Columnas agrupadas | Eje `Calendario[Date]`; valores `kWh generados` y `kWh consumidos` | Si la generación diaria cubre el consumo |
| Líneas | Eje `v_lecturas_5min[hora]`; valores promedio de `generacion_w` y `consumo_w` | Perfil horario |
| Líneas | Eje `Calendario[Date]`; `SoC mínimo` y `SoC promedio`; línea constante en `v_domos[reserva_soc]` | Cuánto se acerca la batería a la reserva |
| Barras | Eje `v_cortes[categoria]`; valor `Cortes del sistema` | Que R-13 corta primero lo prescindible |
| Tarjetas | `kWh generados`, `Horas bajo reserva`, `% acciones automáticas`, `Minutos medios hasta atender` | Resumen del período |

En la página Histórico no actives la actualización automática: sus tablas son importadas y no
cambiarían.
