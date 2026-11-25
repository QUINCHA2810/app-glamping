"""esquema bi: vistas de solo lectura para Power BI y el rol lector_bi

Power BI (DirectQuery para la página En vivo, Import para Histórico) se conecta con un
rol propio, lector_bi, que solo ve las vistas de este esquema. Ver docs/PowerBI.md,
"En vivo con DirectQuery".

Vistas, todas con fechas y horas de America/Bogota (timestamp sin zona):

    bi.v_domos            configuración del domo: batería, perfil, reserva, demostración
    bi.v_estado_actual    por domo: última lectura, minutos desde ella, modo de R-13
    bi.v_lecturas_minuto  últimas 48 h por minuto: generación, consumo y SoC
    bi.v_lecturas_5min    todo el histórico cada 5 minutos, con la energía integrada
    bi.v_dispositivos     dispositivos, sin id_fisico
    bi.v_acciones         acciones sobre dispositivos; del actor, solo el rol
    bi.v_alertas          alertas
    bi.v_cortes           cada corte de R-13 con su duración
    bi.v_pzem             frecuencia y factor de potencia por minuto

Ninguna vista lee usuarios: no hay correos, nombres de cuenta ni hashes. Tampoco
id_fisico (v_dispositivos solo dice si el dispositivo es físico, como dim_dispositivo).

Mismas reglas que servicios/reportes.py y tools/exportar_bi.py, reescritas en SQL
porque una vista no puede llamar a Python:
- Energía: trapecios entre lecturas seguidas con dato; un hueco de más de 10 minutos
  no se integra; cada tramo cuenta en la cubeta de la lectura con la que termina.
- Cortes: desde el apagado de R-13 hasta la siguiente acción sobre la toma o hasta que
  se desactivó, lo que pase primero; si no hay ninguna, hasta ahora.
- Modo: normal sin tomas cortadas por R-13, ahorro si todas son prescindibles, si no
  supervivencia. Una toma está cortada si su última acción es un apagado del sistema.
- Batería y perfil: los de parametros_energeticos; un valor ausente o inválido usa
  lifepo4 y conservador. Las constantes de abajo las compara tests/test_bi.py con
  servicios/categorias.py y tools/exportar_bi.py.
tests/test_bi_postgres.py compara cada vista con esas funciones de Python.

Seguridad:
- Las vistas son del rol que corre la migración (postgres, dueño de las tablas y con
  BYPASSRLS) y NO llevan security_invoker: así leen las tablas aunque tengan RLS sin
  políticas. lector_bi no tiene ningún permiso sobre public.
- lector_bi nace NOLOGIN; el dueño le da LOGIN y contraseña a mano (docs/PowerBI.md).
  Por defecto sus transacciones son de solo lectura y cada consulta se corta a los 30 s.
- PUBLIC, anon, authenticated y service_role no tienen nada en bi, aunque haya
  privilegios por defecto que se lo dieran. El esquema bi no está en los esquemas
  expuestos de la API de Supabase y no debe agregarse.

Índices: solo dos, los que las vistas necesitan además de los que Supabase ya tiene
(creados a mano, fuera de esta cadena): idx_lecturas_{bateria,consumo,fv,sensores}_domo_fecha
e idx_historial_domo_fecha, todos (domo_id, fecha_hora). Con ellos la última lectura de cada
domo y las últimas 48 h ya van por índice. Los nuevos:
- historial_acciones (dispositivo_id, fecha_hora, id): la última acción de cada toma
  (v_estado_actual). El existente empieza por domo_id y no sirve para buscar por toma.
- lecturas_sensores, parcial para el PZEM y por minuto local: Power BI filtra v_pzem por
  minuto_local, una expresión que el índice por (domo_id, fecha_hora) no puede usar.
La migración no depende de los índices existentes: sin ellos las vistas funcionan, más lentas.
CREATE INDEX sin CONCURRENTLY bloquea las escrituras de esa tabla mientras se construye: con
el tamaño actual, segundos.

Consulta previa de solo lectura: tests/consulta_previa_a9e4c1f7b3d2.sql.

Migración escrita a mano (sin --autogenerate), ver backend/README.md. Solo Postgres.

downgrade() elimina las vistas, la función, el esquema bi, los índices y el rol
lector_bi, aunque ya tenga LOGIN. Si alguien creó otros objetos en bi o le dio a
lector_bi otros permisos, falla sin tocar nada: hay que quitarlos antes.

Revision ID: a9e4c1f7b3d2
Revises: f7d3b9e5a2c8
Create Date: 2026-11-10 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9e4c1f7b3d2'
down_revision: Union[str, Sequence[str], None] = 'f7d3b9e5a2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROL = 'lector_bi'
ZONA = 'America/Bogota'
# Copias congeladas de servicios/categorias.py y tools/exportar_bi.py (ver tests/test_bi.py).
RESERVA_SOC_POR_BATERIA = {'lifepo4': 20.0, 'agm': 50.0}
TIPO_BATERIA_POR_DEFECTO = 'lifepo4'
PERFILES = ('conservador', 'equilibrado', 'maximo_uso')
PERFIL_POR_DEFECTO = 'conservador'
ORIGEN_SINTETICO = 'Demostración · datos sintéticos'
ORIGEN_REAL = 'Domo del proyecto'
HUECO_MAXIMO_S = 600
VENTANA_EN_VIVO = '48 hours'

# Orden de creación; el downgrade las elimina al revés (unas dependen de otras).
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
    ('historial_acciones_dispositivo_fecha_id_idx', 'historial_acciones', '(dispositivo_id, fecha_hora, id)'),
    (
        'lecturas_sensores_pzem_minuto_idx',
        'lecturas_sensores',
        # El minuto primero: Power BI filtra por tiempo, casi nunca por domo.
        f"((date_trunc('minute', timezone('{ZONA}', fecha_hora))), domo_id) WHERE tipo_sensor = 'pzem004t'",
    ),
)

# Roles de la API de datos de Supabase. En un Postgres sin Supabase no existen.
ROLES_API = ('anon', 'authenticated', 'service_role')


def _lista(valores) -> str:
    return ', '.join(f"'{v}'" for v in valores)


def _local(columna: str) -> str:
    return f"timezone('{ZONA}', {columna})"


_CASO_RESERVA = ' '.join(f"WHEN '{tipo}' THEN {reserva}" for tipo, reserva in RESERVA_SOC_POR_BATERIA.items())

FUNCION = f"""
CREATE FUNCTION bi.json_o_vacio(texto text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
SET search_path = ''
AS $$
-- parametros_energeticos es texto: un JSON inválido o que no sea un objeto cuenta
-- como "nada guardado", igual que servicios/categorias.parametros_energeticos_de.
DECLARE
    valor jsonb;
BEGIN
    IF texto IS NULL THEN
        RETURN '{{}}'::jsonb;
    END IF;
    BEGIN
        valor := texto::jsonb;
    EXCEPTION WHEN others THEN
        RETURN '{{}}'::jsonb;
    END;
    IF jsonb_typeof(valor) <> 'object' THEN
        RETURN '{{}}'::jsonb;
    END IF;
    RETURN valor;
END
$$
"""

SQL_VISTAS = {
    'v_domos': f"""
CREATE VIEW bi.v_domos AS
WITH config AS (
    -- Una fila por domo; si hubiera varias, la más antigua.
    SELECT DISTINCT ON (c.domo_id) c.domo_id, bi.json_o_vacio(c.parametros_energeticos) AS p
    FROM public.configuracion_sistema c
    ORDER BY c.domo_id, c.id
), ajustes AS (
    SELECT
        d.id,
        d.nombre,
        d.ubicacion,
        d.es_demostracion,
        CASE WHEN c.p -> 'bateria' ->> 'tipo' IN ({_lista(RESERVA_SOC_POR_BATERIA)})
             THEN c.p -> 'bateria' ->> 'tipo' ELSE '{TIPO_BATERIA_POR_DEFECTO}' END AS tipo_bateria,
        CASE WHEN jsonb_typeof(c.p -> 'bateria' -> 'capacidad_wh') = 'number'
             THEN (c.p -> 'bateria' ->> 'capacidad_wh')::numeric END AS capacidad_wh,
        CASE WHEN c.p ->> 'perfil_control' IN ({_lista(PERFILES)})
             THEN c.p ->> 'perfil_control' ELSE '{PERFIL_POR_DEFECTO}' END AS perfil_control
    FROM public.domos d
    LEFT JOIN config c ON c.domo_id = d.id
)
SELECT
    id AS domo_id,
    nombre,
    ubicacion,
    es_demostracion,
    CASE WHEN es_demostracion THEN '{ORIGEN_SINTETICO}' ELSE '{ORIGEN_REAL}' END AS origen_datos,
    tipo_bateria,
    capacidad_wh,
    (CASE tipo_bateria {_CASO_RESERVA} END)::numeric AS reserva_soc,
    perfil_control
FROM ajustes
""",
    'v_estado_actual': f"""
CREATE VIEW bi.v_estado_actual AS
SELECT
    d.domo_id,
    d.nombre,
    d.es_demostracion,
    d.reserva_soc,
    b.estado_carga_soc AS soc,
    {_local('b.fecha_hora')} AS fecha_soc_local,
    f.potencia_generada AS generacion_w,
    {_local('f.fecha_hora')} AS fecha_generacion_local,
    c.potencia_consumida AS consumo_w,
    {_local('c.fecha_hora')} AS fecha_consumo_local,
    {_local('u.ultima')} AS ultima_lectura_local,
    round((extract(epoch FROM now() - u.ultima) / 60.0)::numeric, 1) AS minutos_desde_ultima_lectura,
    CASE WHEN t.cortadas = 0 THEN 'normal'
         WHEN t.cortadas = t.prescindibles THEN 'ahorro'
         ELSE 'supervivencia' END AS estado_energetico,
    t.cortadas AS tomas_cortadas
FROM bi.v_domos d
-- La última fila de cada tabla, como GET /domos/{{id}}/estado-general y R-13.
LEFT JOIN LATERAL (
    SELECT l.estado_carga_soc, l.fecha_hora FROM public.lecturas_bateria l
    WHERE l.domo_id = d.domo_id ORDER BY l.fecha_hora DESC, l.id DESC LIMIT 1
) b ON true
LEFT JOIN LATERAL (
    SELECT l.potencia_generada, l.fecha_hora FROM public.lecturas_fotovoltaicas l
    WHERE l.domo_id = d.domo_id ORDER BY l.fecha_hora DESC, l.id DESC LIMIT 1
) f ON true
LEFT JOIN LATERAL (
    SELECT l.potencia_consumida, l.fecha_hora FROM public.lecturas_consumo l
    WHERE l.domo_id = d.domo_id ORDER BY l.fecha_hora DESC, l.id DESC LIMIT 1
) c ON true
CROSS JOIN LATERAL (SELECT GREATEST(b.fecha_hora, f.fecha_hora, c.fecha_hora) AS ultima) u
-- Tomas activas cuya última acción es un apagado del sistema
-- (servicios/control_energetico._tomas_apagadas_por_sistema).
CROSS JOIN LATERAL (
    SELECT count(*) AS cortadas, count(*) FILTER (WHERE x.categoria = 'prescindible') AS prescindibles
    FROM public.dispositivos x
    CROSS JOIN LATERAL (
        SELECT h.rol, h.accion_realizada FROM public.historial_acciones h
        WHERE h.dispositivo_id = x.id ORDER BY h.fecha_hora DESC, h.id DESC LIMIT 1
    ) ultima
    WHERE x.domo_id = d.domo_id AND x.tipo = 'toma' AND x.activo
      AND ultima.rol = 'sistema' AND ultima.accion_realizada = 'apagar'
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
-- Por domo: cada tabla usa su índice (domo_id, fecha_hora) solo para las últimas 48 h.
CROSS JOIN LATERAL (
    SELECT d.id AS domo_id, date_trunc('minute', {_local('x.fecha_hora')}) AS minuto_local,
           x.potencia_generada::float8 AS generacion, NULL::float8 AS consumo, NULL::float8 AS soc
    FROM public.lecturas_fotovoltaicas x
    WHERE x.domo_id = d.id AND x.fecha_hora >= now() - interval '{VENTANA_EN_VIVO}'
    UNION ALL
    SELECT d.id, date_trunc('minute', {_local('x.fecha_hora')}), NULL, x.potencia_consumida::float8, NULL
    FROM public.lecturas_consumo x
    WHERE x.domo_id = d.id AND x.fecha_hora >= now() - interval '{VENTANA_EN_VIVO}'
    UNION ALL
    SELECT d.id, date_trunc('minute', {_local('x.fecha_hora')}), NULL, NULL, x.estado_carga_soc::float8
    FROM public.lecturas_bateria x
    WHERE x.domo_id = d.id AND x.fecha_hora >= now() - interval '{VENTANA_EN_VIVO}'
) l
GROUP BY l.domo_id, l.minuto_local
""",
    'v_lecturas_5min': f"""
CREATE VIEW bi.v_lecturas_5min AS
WITH tramos AS (
    -- Cada lectura con dato y la anterior con dato del mismo domo (servicios/reportes.intervalos).
    SELECT x.domo_id, 'generacion' AS medida, x.fecha_hora AS t, x.potencia_generada::float8 AS v,
           lag(x.fecha_hora) OVER w AS t0, lag(x.potencia_generada::float8) OVER w AS v0
    FROM public.lecturas_fotovoltaicas x
    WHERE x.potencia_generada IS NOT NULL
    WINDOW w AS (PARTITION BY x.domo_id ORDER BY x.fecha_hora, x.id)
    UNION ALL
    SELECT x.domo_id, 'consumo', x.fecha_hora, x.potencia_consumida::float8,
           lag(x.fecha_hora) OVER w, lag(x.potencia_consumida::float8) OVER w
    FROM public.lecturas_consumo x
    WHERE x.potencia_consumida IS NOT NULL
    WINDOW w AS (PARTITION BY x.domo_id ORDER BY x.fecha_hora, x.id)
    UNION ALL
    SELECT x.domo_id, 'soc', x.fecha_hora, x.estado_carga_soc::float8,
           lag(x.fecha_hora) OVER w, lag(x.estado_carga_soc::float8) OVER w
    FROM public.lecturas_bateria x
    WHERE x.estado_carga_soc IS NOT NULL
    WINDOW w AS (PARTITION BY x.domo_id ORDER BY x.fecha_hora, x.id)
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
    x.prioridad,
    x.umbral_apagado_soc,
    x.umbral_reactivacion_soc
FROM public.dispositivos x
""",
    'v_acciones': f"""
CREATE VIEW bi.v_acciones AS
SELECT
    h.id AS accion_id,
    h.domo_id,
    h.dispositivo_id,
    {_local('h.fecha_hora')} AS momento_local,
    {_local('h.fecha_hora')}::date AS fecha,
    extract(hour FROM {_local('h.fecha_hora')})::int AS hora,
    h.rol,
    h.accion_realizada AS accion,
    (h.rol = 'sistema') AS automatica,
    h.resultado
FROM public.historial_acciones h
""",
    'v_alertas': f"""
CREATE VIEW bi.v_alertas AS
SELECT
    a.id AS alerta_id,
    a.domo_id,
    {_local('a.fecha_hora')} AS momento_local,
    {_local('a.fecha_hora')}::date AS fecha,
    extract(hour FROM {_local('a.fecha_hora')})::int AS hora,
    a.tipo_alerta,
    a.nivel_prioridad,
    a.estado_alerta,
    a.variable_asociada,
    a.valor_detectado,
    a.valor_limite,
    {_local('a.fecha_atendida')} AS atendida_local,
    round((extract(epoch FROM a.fecha_atendida - a.fecha_hora) / 60.0)::numeric, 3) AS minutos_hasta_atendida
FROM public.alertas a
""",
    'v_cortes': f"""
CREATE VIEW bi.v_cortes AS
WITH acciones AS (
    -- servicios/reportes.intervalos_de_corte sin límites de rango: desde siempre hasta ahora.
    SELECT h.id, h.domo_id, h.dispositivo_id, h.rol, h.accion_realizada, h.fecha_hora,
           lead(h.fecha_hora) OVER (PARTITION BY h.dispositivo_id ORDER BY h.fecha_hora, h.id) AS siguiente
    FROM public.historial_acciones h
    WHERE h.dispositivo_id IS NOT NULL AND h.fecha_hora < now()
), cortes AS (
    SELECT a.id, a.domo_id, a.dispositivo_id, x.categoria, a.fecha_hora AS inicio, a.siguiente, x.desactivado_en,
           GREATEST(a.fecha_hora, LEAST(now(), a.siguiente, x.desactivado_en)) AS fin
    FROM acciones a
    JOIN public.dispositivos x ON x.id = a.dispositivo_id
    WHERE a.rol = 'sistema' AND a.accion_realizada = 'apagar'
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
    -- Una fila por variable cada 60 s (firmware/README.md); aquí, una por minuto.
    SELECT
        s.domo_id,
        date_trunc('minute', {_local('s.fecha_hora')}) AS minuto_local,
        round((avg(s.valor) FILTER (WHERE s.variable_medida = 'frecuencia'))::numeric, 2) AS frecuencia_hz,
        round((avg(s.valor) FILTER (WHERE s.variable_medida = 'factor_potencia'))::numeric, 3) AS factor_potencia
    FROM public.lecturas_sensores s
    WHERE s.tipo_sensor = 'pzem004t' AND s.valor IS NOT NULL
      AND s.variable_medida IN ('frecuencia', 'factor_potencia')
    GROUP BY s.domo_id, date_trunc('minute', {_local('s.fecha_hora')})
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
    op.execute(FUNCION)
    for vista in VISTAS:
        op.execute(SQL_VISTAS[vista])

    # Nadie más que lector_bi: ni PUBLIC ni los roles de la API, aunque algún privilegio
    # por defecto se los hubiera dado al crear las vistas o la función.
    op.execute('REVOKE ALL ON ALL TABLES IN SCHEMA bi FROM PUBLIC')
    op.execute('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bi FROM PUBLIC')
    op.execute(f"""
DO $$
DECLARE
    rol text;
BEGIN
    FOREACH rol IN ARRAY '{_ROLES_API_SQL}'::text[] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rol) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA bi FROM %I', rol);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA bi FROM %I', rol);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bi FROM %I', rol);
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
    # Una vista ejecuta sus funciones con los permisos de quien consulta.
    op.execute(f'GRANT EXECUTE ON FUNCTION bi.json_o_vacio(text) TO {ROL}')


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name != 'postgresql':
        return

    for vista in reversed(VISTAS):
        op.execute(f'DROP VIEW bi.{vista}')
    op.execute('DROP FUNCTION bi.json_o_vacio(text)')
    # Sin CASCADE: si alguien creó algo más en bi, falla y no se borra nada.
    op.execute('DROP SCHEMA bi')
    for nombre, _, _ in reversed(INDICES):
        op.execute(f'DROP INDEX public.{nombre}')
    # Con LOGIN o sin él. Falla si tiene otros permisos o es dueño de algo.
    op.execute(f'DROP ROLE {ROL}')
