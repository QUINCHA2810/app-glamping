-- Solo lectura. Antes de aplicar f7d3b9e5a2c8: listo_para_migrar debe salir true.
SELECT
    (SELECT string_agg(version_num, ', ') FROM public.alembic_version) AS version_actual,
    (SELECT count(*) = 1 AND bool_and(version_num = 'e6c2a8d4f1b9') FROM public.alembic_version) AS version_correcta,
    NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'domos' AND column_name = 'es_demostracion'
    ) AS sin_es_demostracion,
    (SELECT count(*) FROM public.domos) AS domos,
    (SELECT count(*) FROM public.domos WHERE nombre = 'Domo demostración') AS ya_hay_uno_con_el_nombre_demo,
    (
        (SELECT count(*) = 1 AND bool_and(version_num = 'e6c2a8d4f1b9') FROM public.alembic_version)
        AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'domos' AND column_name = 'es_demostracion'
        )
    ) AS listo_para_migrar;
