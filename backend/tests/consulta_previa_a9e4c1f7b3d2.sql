-- Solo lectura. Antes de aplicar a9e4c1f7b3d2: listo_para_migrar debe salir true.
-- Además revisa con_indices_existentes_de_lecturas (debe ser true), indices_existentes
-- (si ya hay uno equivalente a los dos nuevos, avísame) y
-- esquemas_expuestos_api (no debe incluir bi).
SELECT
    (SELECT string_agg(version_num, ', ') FROM public.alembic_version) AS version_actual,
    (SELECT count(*) = 1 AND bool_and(version_num = 'f7d3b9e5a2c8') FROM public.alembic_version) AS version_correcta,
    current_setting('server_version') AS version_postgres,
    current_user AS usuario_actual,
    (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user) AS puede_crear_roles,
    NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bi') AS sin_esquema_bi,
    NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_bi') AS sin_rol_lector_bi,
    NOT EXISTS (
        SELECT 1 FROM pg_class
        WHERE relkind = 'i' AND relnamespace = 'public'::regnamespace AND relname IN (
            'historial_acciones_dispositivo_fecha_id_idx', 'lecturas_sensores_pzem_minuto_idx'
        )
    ) AS sin_indices_nuevos,
    -- Informativo: la migración no los necesita, pero sin ellos las vistas en vivo son lentas.
    (
        SELECT count(*) = 5 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname IN (
            'idx_lecturas_bateria_domo_fecha', 'idx_lecturas_consumo_domo_fecha', 'idx_lecturas_fv_domo_fecha',
            'idx_lecturas_sensores_domo_fecha', 'idx_historial_domo_fecha'
        )
    ) AS con_indices_existentes_de_lecturas,
    (
        SELECT string_agg(tablename || ': ' || indexdef, E'\n' ORDER BY tablename, indexname)
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename IN (
            'lecturas_bateria', 'lecturas_fotovoltaicas', 'lecturas_consumo', 'lecturas_sensores', 'historial_acciones'
        )
    ) AS indices_existentes,
    (
        SELECT string_agg(c.relname || ' ~' || c.reltuples::bigint, ', ' ORDER BY c.relname)
        FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' AND c.relname IN (
            'lecturas_bateria', 'lecturas_fotovoltaicas', 'lecturas_consumo', 'lecturas_sensores', 'historial_acciones'
        )
    ) AS filas_estimadas,
    (
        SELECT count(*) FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' AND NOT c.relrowsecurity
    ) AS tablas_public_sin_rls,
    (
        SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a
        WHERE c.relnamespace = 'public'::regnamespace AND a.grantee = 0
    ) AS permisos_de_tabla_para_public,
    (
        SELECT count(*) FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef
    ) AS funciones_security_definer_en_public,
    has_schema_privilege('public', 'public', 'CREATE') AS cualquiera_puede_crear_en_public,
    current_setting('pgrst.db_schemas', true) AS esquemas_expuestos_api,
    (
        SELECT string_agg(array_to_string(s.setconfig, '; '), ' | ')
        FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
        WHERE r.rolname = 'authenticator'
    ) AS ajustes_de_authenticator,
    (
        (SELECT count(*) = 1 AND bool_and(version_num = 'f7d3b9e5a2c8') FROM public.alembic_version)
        AND current_setting('server_version_num')::int >= 140000
        AND (SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user)
        AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'bi')
        AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_bi')
        AND NOT EXISTS (
            SELECT 1 FROM pg_class
            WHERE relkind = 'i' AND relnamespace = 'public'::regnamespace AND relname IN (
                'historial_acciones_dispositivo_fecha_id_idx', 'lecturas_sensores_pzem_minuto_idx'
            )
        )
    ) AS listo_para_migrar;
