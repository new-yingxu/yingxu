BEGIN;
CREATE OR REPLACE FUNCTION public.yingxu_set_preferences(
  p_owner TEXT, p_sync_records BOOLEAN, p_sync_agent_sessions BOOLEAN,
  p_sync_thumbnails BOOLEAN, p_sync_paths BOOLEAN
) RETURNS VOID
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $function$
BEGIN
  INSERT INTO public.sync_preferences (owner_id, sync_records, sync_agent_sessions, sync_thumbnails, sync_paths, updated_at)
  VALUES (p_owner, p_sync_records, p_sync_agent_sessions, p_sync_thumbnails, p_sync_paths, CURRENT_TIMESTAMP)
  ON CONFLICT (owner_id) DO UPDATE SET
    sync_records=EXCLUDED.sync_records,
    sync_agent_sessions=EXCLUDED.sync_agent_sessions,
    sync_thumbnails=EXCLUDED.sync_thumbnails,
    sync_paths=EXCLUDED.sync_paths,
    updated_at=EXCLUDED.updated_at;
  IF NOT p_sync_paths THEN
    UPDATE public.sync_events
    SET body_json=jsonb_set(body_json::jsonb, '{payload}', (body_json->'payload')::jsonb - 'source' - 'paths')::json
    WHERE owner_id=p_owner;
  END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.yingxu_set_preferences(TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_set_preferences(TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN) TO service_role;
INSERT INTO schema_migrations (version, applied_at) VALUES (3,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
