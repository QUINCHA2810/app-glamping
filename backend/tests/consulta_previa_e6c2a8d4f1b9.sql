-- Solo lectura. Antes de aplicar e6c2a8d4f1b9: listo_para_migrar debe salir true.
SELECT
    (SELECT string_agg(version_num, ', ') FROM public.alembic_version) AS version_actual,
    (SELECT count(*) = 1 AND bool_and(version_num = 'd5b1f3a9c2e7') FROM public.alembic_version) AS version_correcta,
    NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'dispositivos' AND column_name = 'desactivado_en'
    ) AS sin_desactivado_en,
    NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'usuarios' AND column_name = 'debe_cambiar_contrasena'
    ) AS sin_debe_cambiar_contrasena,
    (SELECT count(*) FROM public.dispositivos WHERE NOT activo) AS dispositivos_inactivos,
    (
        SELECT count(*) FROM public.dispositivos AS d
        WHERE NOT d.activo AND NOT EXISTS (SELECT 1 FROM public.historial_acciones AS h WHERE h.dispositivo_id = d.id)
    ) AS inactivos_sin_historial,
    (
        SELECT string_agg(
            d.id || ' ' || d.nombre || ': ' || coalesce(
                (SELECT max(h.fecha_hora) FROM public.historial_acciones AS h WHERE h.dispositivo_id = d.id)::text,
                'la hora de la migración'
            ),
            '; ' ORDER BY d.id
        )
        FROM public.dispositivos AS d WHERE NOT d.activo
    ) AS relleno_previsto,
    (
        (SELECT count(*) = 1 AND bool_and(version_num = 'd5b1f3a9c2e7') FROM public.alembic_version)
        AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (('dispositivos', 'desactivado_en'), ('usuarios', 'debe_cambiar_contrasena'))
        )
    ) AS listo_para_migrar;
