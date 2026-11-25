"""esquema bi sobre el modelo v2: las mismas 9 vistas de v1 para Power BI y el rol lector_bi

Ronda DB-3. Es la migración a9e4c1f7b3d2 de v1 reescrita sobre las 7 tablas de v2, con
los MISMOS nombres de vista y de columna, para que el informe de Power BI no se rehaga.
Ver docs/PowerBI.md, "En vivo con DirectQuery", y docs/modelo_v2/MAPEO.md, sección 8.

Vistas, todas con fechas y horas de America/Bogota (timestamp sin zona):

    bi.v_domos            configuración del domo: batería, perfil, reserva, demostración
    bi.v_estado_actual    por domo: último valor de cada medida, minutos desde él, modo de R-13
    bi.v_lecturas_minuto  últimas 48 h por minuto: generación, consumo y SoC
    bi.v_lecturas_5min    todo el histórico cada 5 minutos, con la energía integrada
    bi.v_dispositivos     dispositivos, sin id_fisico; umbrales y prioridad CALCULADOS
    bi.v_acciones         acciones sobre dispositivos; del actor, solo el rol
    bi.v_alertas          alertas
    bi.v_cortes           cada corte de R-13 con su duración
    bi.v_pzem             frecuencia y factor de potencia por minuto

Qué cambia por debajo (las columnas no):
- Las lecturas salen de `mediciones` (una fila por domo y bloque de 10 s). Cada medida se
  toma de su columna, sin las filas en que viene NULL.
- v_domos: batería y perfil son columnas de `domos` (v1 los leía del JSON de
  configuracion_sistema); el CHECK de la tabla ya garantiza valores válidos.
- v_estado_actual: el ÚLTIMO VALOR NO NULO de cada medida, con su fecha (v1: la última
  fila de cada tabla, aunque trajera null). Un ESP32 sin sensor que publica null no
  borra el último dato, pero su fecha dice lo viejo que es: minutos_desde_ultima_lectura
  y las fechas *_local son las de esos valores. La app, en cambio, no muestra como
  actual nada de más de 2 minutos (servicios/mediciones.VIGENCIA_S).
- v_dispositivos: prioridad, umbral_apagado_soc y umbral_reactivacion_soc no se guardan:
  se calculan como servicios/categorias.preset_de con la batería y el perfil del domo.
- v_acciones y v_cortes: el domo sale de `dispositivos` (acciones no tiene domo_id).
  `rol` conserva los valores de v1: 'sistema', 'dueño', 'administrador' (actor_tipo
  'dueno' se muestra como 'dueño', igual que la API).
- v_alertas: las columnas de v1 son alias de las de v2 (tipo, nivel, estado, variable,
  limite).

Mismas reglas que servicios/reportes.py y tools/exportar_bi.py, reescritas en SQL
porque una vista no puede llamar a Python:
- Energía: trapecios entre lecturas seguidas con dato; un hueco de más de 10 minutos
  no se integra; cada tramo cuenta en la cubeta de la lectura con la que termina.
- Cortes: desde el apagado de R-13 hasta la siguiente acción sobre la toma o hasta que
  se desactivó, lo que pase primero; si no hay ninguna, hasta ahora.
- Modo: normal sin tomas cortadas por R-13, ahorro si todas son prescindibles, si no
  supervivencia. Una toma está cortada si su última acción es un apagado del sistema.
Las constantes de abajo las compara tests/test_bi_v2.py con servicios/categorias.py,
servicios/reportes.py y tools/exportar_bi.py. tests/test_esquema_v2_postgres.py
(clase BiSobrePostgres) compara las vistas con esas funciones de Python.

Seguridad (igual que en v1):
- Las vistas son del rol que corre la migración (postgres, dueño de las tablas y con
  BYPASSRLS) y NO llevan security_invoker: así leen las tablas aunque tengan RLS sin
  políticas. lector_bi no tiene ningún permiso sobre public.
- lector_bi nace NOLOGIN y SIN contraseña; el dueño le da LOGIN y contraseña a mano en
  el SQL Editor y borra el snippet (docs/PowerBI.md, 7.3). Por defecto sus transacciones
  son de solo lectura y cada consulta se corta a los 30 s.
- PUBLIC, anon, authenticated y service_role no tienen nada en bi, aunque haya
  privilegios por defecto que se lo dieran. El esquema bi no está en los esquemas
  expuestos de la API de Supabase y no debe agregarse.
- Los roles son del servidor, no de la base: este lector_bi es el del proyecto v2 de
  Supabase, distinto del de v1 (otra contraseña, otro usuario del pooler).

Índices: la última acción de cada toma ya tiene acciones_dispositivo_fecha_idx
(revisión base) y las lecturas por domo y fecha la UNIQUE (domo_id, fecha_hora). Uno
nuevo, como en v1: parcial para el PZEM y por minuto local, porque Power BI filtra v_pzem
por minuto_local. CREATE INDEX sin CONCURRENTLY bloquea las escrituras de mediciones
mientras se construye: con el tamaño actual, segundos (hágase con el listener detenido,
como dice docs/modelo_v2/CAMBIO_DEFINITIVO.md).

Migración escrita a mano (sin --autogenerate). Solo Postgres.

downgrade() elimina las vistas, el esquema bi, el índice y el rol lector_bi, aunque ya
tenga LOGIN. Si alguien creó otros objetos en bi o le dio a lector_bi otros permisos,
falla sin tocar nada: hay que quitarlos antes.

Revision ID: 4e8b2c6d9f17
Revises: 7c3d9a1e4b52
Create Date: 2026-11-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = '4e8b2c6d9f17'
down_revision: Union[str, Sequence[str], None] = '7c3d9a1e4b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROL = 'lector_bi'
ZONA = 'America/Bogota'
# Copias congeladas de servicios/categorias.py, servicios/reportes.py y tools/exportar_bi.py
# (tests/test_bi_v2.py las compara).
RESERVA_SOC_POR_BATERIA = {'lifepo4': 20.0, 'agm': 50.0}
PRIORIDAD_POR_CATEGORIA = {'esencial': 40, 'importante': 30, 'confort': 20, 'prescindible': 10}
SOBRE_LA_RESERVA = {
    'conservador': {'importante': 0.0, 'confort': 15.0, 'prescindible': 30.0},
    'equilibrado': {'importante': 0.0, 'confort': 10.0, 'prescindible': 20.0},
    'maximo_uso': {'importante': 0.0, 'confort': 5.0, 'prescindible': 10.0},
}
BANDA_DE_REACTIVACION = 10.0
ORIGEN_SINTETICO = 'Demostración · datos sintéticos'
ORIGEN_REAL = 'Domo del proyecto'
HUECO_MAXIMO_S = 600
VENTANA_EN_VIVO = '48 hours'

# Los mismos nombres y el mismo orden que v1; el downgrade las elimina al revés.
VISTAS = (
    'v_domos',
    'v_estado_actual',
    'v_lecturas_minuto',
    'v_lecturas_5min',
    'v_dispositivos',
    'v_acciones',
    'v_alertas',
    'v_cortes',
    'v_pzem',
)

INDICES = (
    (
        'mediciones_pzem_minuto_idx',
        'mediciones',
        # El minuto primero: Power BI filtra por tiempo, casi nunca por domo.
        f"((date_trunc('minute', timezone('{ZONA}', fecha_hora))), domo_id) "
        "WHERE frecuencia_hz IS NOT NULL OR factor_potencia IS NOT NULL",
    ),
)

# Roles de la API de datos de Supabase. En un Postgres sin Supabase no existen.
ROLES_API = ('anon', 'authenticated', 'service_role')


def _local(columna: str) -> str:
    return f"timezone('{ZONA}', {columna})"


_CASO_RESERVA = ' '.join(f"WHEN '{tipo}' THEN {reserva}" for tipo, reserva in RESERVA_SOC_POR_BATERIA.items())
_CASO_PRIORIDAD = ' '.join(f"WHEN '{c}' THEN {p}" for c, p in PRIORIDAD_POR_CATEGORIA.items())
_CASO_SOBRE_RESERVA = ' '.join(
    f"WHEN d.perfil = '{perfil}' AND x.categoria = '{categoria}' THEN {margen}"
    for perfil, margenes in SOBRE_LA_RESERVA.items()
    for categoria, margen in margenes.items()
)


def _ultimo(columna: str, alias: str) -> str:
    """LATERAL con el último valor no nulo de una columna de mediciones y su fecha."""
    return f"""LEFT JOIN LATERAL (
    SELECT m.{columna} AS valor, m.fecha_hora FROM public.mediciones m
    WHERE m.domo_id = d.domo_id AND m.{columna} IS NOT NULL ORDER BY m.fecha_hora DESC LIMIT 1
) {alias} ON true"""


SQL_VISTAS = {
    'v_domos': f"""
CREATE VIEW bi.v_domos AS
SELECT
    d.id AS domo_id,
    d.nombre,
    d.ubicacion,
    d.es_demostracion,
    CASE WHEN d.es_demostracion THEN '{ORIGEN_SINTETICO}' ELSE '{ORIGEN_REAL}' END AS origen_datos,
    d.bateria_tipo AS tipo_bateria,
    d.bateria_capacidad_wh::numeric AS capacidad_wh,
    (CASE d.bateria_tipo {_CASO_RESERVA} END)::numeric AS reserva_soc,
    d.perfil AS perfil_control
FROM public.domos d
""",
    'v_estado_actual': f"""
CREATE VIEW bi.v_estado_actual AS
SELECT
    d.domo_id,
    d.nombre,
    d.es_demostracion,
    d.reserva_soc,
    b.valor AS soc,
    {_local('b.fecha_hora')} AS fecha_soc_local,
    f.valor AS generacion_w,
    {_local('f.fecha_hora')} AS fecha_generacion_local,
    c.valor AS consumo_w,
    {_local('c.fecha_hora')} AS fecha_consumo_local,
    {_local('u.ultima')} AS ultima_lectura_local,
    round((extract(epoch FROM now() - u.ultima) / 60.0)::numeric, 1) AS minutos_desde_ultima_lectura,
    CASE WHEN t.cortadas = 0 THEN 'normal'
         WHEN t.cortadas = t.prescindibles THEN 'ahorro'
         ELSE 'supervivencia' END AS estado_energetico,
    t.cortadas AS tomas_cortadas
FROM bi.v_domos d
-- El último valor no nulo de cada medida, con su fecha (ver el docstring).
{_ultimo('soc_pct', 'b')}
{_ultimo('potencia_fv_w', 'f')}
{_ultimo('potencia_consumo_w', 'c')}
CROSS JOIN LATERAL (SELECT GREATEST(b.fecha_hora, f.fecha_hora, c.fecha_hora) AS ultima) u
-- Tomas activas cuya última acción es un apagado del sistema
-- (servicios/control_energetico._tomas_apagadas_por_sistema).
CROSS JOIN LATERAL (
    SELECT count(*) AS cortadas, count(*) FILTER (WHERE x.categoria = 'prescindible') AS prescindibles
    FROM public.dispositivos x
    CROSS JOIN LATERAL (
        SELECT a.actor_tipo, a.accion FROM public.acciones a
        WHERE a.dispositivo_id = x.id ORDER BY a.fecha_hora DESC, a.id DESC LIMIT 1
    ) ultima
    WHERE x.domo_id = d.domo_id AND x.tipo = 'toma' AND x.activo
      AND ultima.actor_tipo = 'sistema' AND ultima.accion = 'apagar'
) t
""",
    'v_lecturas_minuto': f"""
CREATE VIEW bi.v_lecturas_minuto AS
SELECT
    l.domo_id,
    l.minuto_local,
    l.minuto_local::date AS fecha,
    extract(hour FROM l.minuto_local)::int AS hora,
    round(avg(l.generacion)::numeric, 3) AS generacion_w,
    round(avg(l.consumo)::numeric, 3) AS consumo_w,
    round(avg(l.soc)::numeric, 3) AS soc
FROM public.domos d
-- Por domo: la UNIQUE (domo_id, fecha_hora) sirve solo para las últimas 48 h.
CROSS JOIN LATERAL (
    SELECT d.id AS domo_id, date_trunc('minute', {_local('m.fecha_hora')}) AS minuto_local,
           m.potencia_fv_w AS generacion, m.potencia_consumo_w AS consumo, m.soc_pct AS soc
    FROM public.mediciones m
    WHERE m.domo_id = d.id AND m.fecha_hora >= now() - interval '{VENTANA_EN_VIVO}'
      AND (m.potencia_fv_w IS NOT NULL OR m.potencia_consumo_w IS NOT NULL OR m.soc_pct IS NOT NULL)
) l
GROUP BY l.domo_id, l.minuto_local
""",
    'v_lecturas_5min': f"""
CREATE VIEW bi.v_lecturas_5min AS
WITH tramos AS (
    -- Cada valor con dato y el anterior con dato del mismo domo (servicios/reportes.intervalos).
    SELECT m.domo_id, 'generacion' AS medida, m.fecha_hora AS t, m.potencia_fv_w AS v,
           lag(m.fecha_hora) OVER w AS t0, lag(m.potencia_fv_w) OVER w AS v0
    FROM public.mediciones m
    WHERE m.potencia_fv_w IS NOT NULL
    WINDOW w AS (PARTITION BY m.domo_id ORDER BY m.fecha_hora, m.id)
    UNION ALL
    SELECT m.domo_id, 'consumo', m.fecha_hora, m.potencia_consumo_w,
           lag(m.fecha_hora) OVER w, lag(m.potencia_consumo_w) OVER w
    FROM public.mediciones m
    WHERE m.potencia_consumo_w IS NOT NULL
    WINDOW w AS (PARTITION BY m.domo_id ORDER BY m.fecha_hora, m.id)
    UNION ALL
    SELECT m.domo_id, 'soc', m.fecha_hora, m.soc_pct,
           lag(m.fecha_hora) OVER w, lag(m.soc_pct) OVER w
    FROM public.mediciones m
    WHERE m.soc_pct IS NOT NULL
    WINDOW w AS (PARTITION BY m.domo_id ORDER BY m.fecha_hora, m.id)
), medidos AS (
    SELECT
        tr.domo_id,
        tr.medida,
        -- Bogotá está a -5 h exactas: la cubeta UTC de 5 minutos es también la local.
        date_bin('5 minutes', tr.t, timestamptz '2000-01-01 00:00:00+00') AS cubeta,
        tr.v,
        tr.v0,
        extract(epoch FROM tr.t - tr.t0)::float8 AS segundos
    FROM tramos tr
), cubetas AS (
    SELECT
        m.domo_id,
        m.cubeta,
        avg(m.v) FILTER (WHERE m.medida = 'generacion') AS generacion_w,
        avg(m.v) FILTER (WHERE m.medida = 'consumo') AS consumo_w,
        avg(m.v) FILTER (WHERE m.medida = 'soc') AS soc,
        sum((m.v + m.v0) / 2.0 * m.segundos / 3600.0)
            FILTER (WHERE m.medida = 'generacion' AND m.segundos <= {HUECO_MAXIMO_S}) AS generacion_wh,
        sum((m.v + m.v0) / 2.0 * m.segundos / 3600.0)
            FILTER (WHERE m.medida = 'consumo' AND m.segundos <= {HUECO_MAXIMO_S}) AS consumo_wh,
        sum(m.segundos)
            FILTER (WHERE m.medida = 'soc' AND m.segundos <= {HUECO_MAXIMO_S} AND m.v0 < d.reserva_soc) AS segundos_bajo_reserva
    FROM medidos m
    JOIN bi.v_domos d ON d.domo_id = m.domo_id
    GROUP BY m.domo_id, m.cubeta
)
SELECT
    domo_id,
    {_local('cubeta')} AS inicio_local,
    {_local('cubeta')}::date AS fecha,
    extract(hour FROM {_local('cubeta')})::int AS hora,
    round(generacion_w::numeric, 3) AS generacion_w,
    round(consumo_w::numeric, 3) AS consumo_w,
    round(soc::numeric, 3) AS soc,
    round(coalesce(generacion_wh, 0)::numeric, 4) AS generacion_wh,
    round(coalesce(consumo_wh, 0)::numeric, 4) AS consumo_wh,
    round((coalesce(segundos_bajo_reserva, 0) / 60.0)::numeric, 3) AS minutos_bajo_reserva
FROM cubetas
""",
    'v_dispositivos': f"""
CREATE VIEW bi.v_dispositivos AS
SELECT
    x.id AS dispositivo_id,
    x.domo_id,
    x.nombre,
    x.tipo,
    x.categoria,
    x.ubicacion,
    x.estado,
    x.activo,
    {_local('x.desactivado_en')} AS desactivado_en_local,
    -- Si corresponde a un dispositivo físico; su identificador no sale.
    (x.id_fisico IS NOT NULL) AS "real",
    -- servicios/categorias.preset_de: no se guardan, se calculan con la batería y el perfil.
    (CASE x.categoria {_CASO_PRIORIDAD} END) AS prioridad,
    u.apagado AS umbral_apagado_soc,
    u.apagado + {BANDA_DE_REACTIVACION} AS umbral_reactivacion_soc
FROM public.dispositivos x
JOIN public.domos d ON d.id = x.domo_id
CROSS JOIN LATERAL (
    SELECT CASE WHEN x.categoria = 'esencial' THEN NULL
                ELSE (CASE d.bateria_tipo {_CASO_RESERVA} END) + (CASE {_CASO_SOBRE_RESERVA} END)
           END::numeric AS apagado
) u
""",
    'v_acciones': f"""
CREATE VIEW bi.v_acciones AS
SELECT
    a.id AS accion_id,
    x.domo_id,
    a.dispositivo_id,
    {_local('a.fecha_hora')} AS momento_local,
    {_local('a.fecha_hora')}::date AS fecha,
    extract(hour FROM {_local('a.fecha_hora')})::int AS hora,
    -- Los valores de v1 ('dueño' con ñ), como los devuelve la API.
    CASE a.actor_tipo WHEN 'dueno' THEN 'dueño' ELSE a.actor_tipo END AS rol,
    a.accion,
    (a.actor_tipo = 'sistema') AS automatica,
    a.resultado
FROM public.acciones a
JOIN public.dispositivos x ON x.id = a.dispositivo_id
""",
    'v_alertas': f"""
CREATE VIEW bi.v_alertas AS
SELECT
    a.id AS alerta_id,
    a.domo_id,
    {_local('a.fecha_hora')} AS momento_local,
    {_local('a.fecha_hora')}::date AS fecha,
    extract(hour FROM {_local('a.fecha_hora')})::int AS hora,
    a.tipo AS tipo_alerta,
    a.nivel AS nivel_prioridad,
    a.estado AS estado_alerta,
    a.variable AS variable_asociada,
    a.valor_detectado,
    a.limite AS valor_limite,
    {_local('a.fecha_atendida')} AS atendida_local,
    round((extract(epoch FROM a.fecha_atendida - a.fecha_hora) / 60.0)::numeric, 3) AS minutos_hasta_atendida
FROM public.alertas a
""",
    'v_cortes': f"""
CREATE VIEW bi.v_cortes AS
WITH acciones AS (
    -- servicios/reportes.intervalos_de_corte sin límites de rango: desde siempre hasta ahora.
    SELECT a.id, x.domo_id, a.dispositivo_id, a.actor_tipo, a.accion, a.fecha_hora,
           lead(a.fecha_hora) OVER (PARTITION BY a.dispositivo_id ORDER BY a.fecha_hora, a.id) AS siguiente
    FROM public.acciones a
    JOIN public.dispositivos x ON x.id = a.dispositivo_id
    WHERE a.fecha_hora < now()
), cortes AS (
    SELECT a.id, a.domo_id, a.dispositivo_id, x.categoria, a.fecha_hora AS inicio, a.siguiente, x.desactivado_en,
           GREATEST(a.fecha_hora, LEAST(now(), a.siguiente, x.desactivado_en)) AS fin
    FROM acciones a
    JOIN public.dispositivos x ON x.id = a.dispositivo_id
    WHERE a.actor_tipo = 'sistema' AND a.accion = 'apagar'
)
SELECT
    id AS accion_id,
    domo_id,
    dispositivo_id,
    categoria,
    {_local('inicio')} AS inicio_local,
    {_local('fin')} AS fin_local,
    {_local('inicio')}::date AS fecha,
    extract(hour FROM {_local('inicio')})::int AS hora,
    round((extract(epoch FROM fin - inicio) / 3600.0)::numeric, 4) AS horas_apagada,
    (siguiente IS NULL AND desactivado_en IS NULL) AS vigente
FROM cortes
""",
    'v_pzem': f"""
CREATE VIEW bi.v_pzem AS
SELECT
    p.domo_id,
    p.minuto_local,
    p.minuto_local::date AS fecha,
    extract(hour FROM p.minuto_local)::int AS hora,
    p.frecuencia_hz,
    p.factor_potencia
FROM (
    -- El firmware manda cada variable cada 60 s (firmware/README.md); aquí, una fila por minuto.
    SELECT
        m.domo_id,
        date_trunc('minute', {_local('m.fecha_hora')}) AS minuto_local,
        round(avg(m.frecuencia_hz)::numeric, 2) AS frecuencia_hz,
        round(avg(m.factor_potencia)::numeric, 3) AS factor_potencia
    FROM public.mediciones m
    WHERE m.frecuencia_hz IS NOT NULL OR m.factor_potencia IS NOT NULL
    GROUP BY m.domo_id, date_trunc('minute', {_local('m.fecha_hora')})
) p
""",
}

_ROLES_API_SQL = '{' + ','.join(ROLES_API) + '}'


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name != 'postgresql':
        return

    for nombre, tabla, definicion in INDICES:
        op.execute(f'CREATE INDEX {nombre} ON public.{tabla} {definicion}')

    op.execute('CREATE SCHEMA bi')
    op.execute('REVOKE ALL ON SCHEMA bi FROM PUBLIC')
    for vista in VISTAS:
        op.execute(SQL_VISTAS[vista])

    # Nadie más que lector_bi: ni PUBLIC ni los roles de la API, aunque algún privilegio
    # por defecto se los hubiera dado al crear las vistas.
    op.execute('REVOKE ALL ON ALL TABLES IN SCHEMA bi FROM PUBLIC')
    op.execute(f"""
DO $$
DECLARE
    rol text;
BEGIN
    FOREACH rol IN ARRAY '{_ROLES_API_SQL}'::text[] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rol) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA bi FROM %I', rol);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA bi FROM %I', rol);
        END IF;
    END LOOP;
END
$$
""")

    op.execute(f'CREATE ROLE {ROL} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS')
    op.execute(f'ALTER ROLE {ROL} SET default_transaction_read_only = on')
    op.execute(f"ALTER ROLE {ROL} SET statement_timeout = '30s'")
    op.execute(f'GRANT USAGE ON SCHEMA bi TO {ROL}')
    for vista in VISTAS:
        op.execute(f'GRANT SELECT ON bi.{vista} TO {ROL}')


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name != 'postgresql':
        return

    for vista in reversed(VISTAS):
        op.execute(f'DROP VIEW bi.{vista}')
    # Sin CASCADE: si alguien creó algo más en bi, falla y no se borra nada.
    op.execute('DROP SCHEMA bi')
    for nombre, _, _ in reversed(INDICES):
        op.execute(f'DROP INDEX public.{nombre}')
    # Con LOGIN o sin él. Falla si tiene otros permisos o es dueño de algo.
    op.execute(f'DROP ROLE {ROL}')
