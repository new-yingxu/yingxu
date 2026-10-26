-- Local preparation only. Requires v10; no deployment performed.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=10) THEN RAISE EXCEPTION 'v10 required'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.agent_graph_checkpoints (
 owner_id VARCHAR(160) NOT NULL, session_id VARCHAR(160) NOT NULL,
 thread_id VARCHAR(160) NOT NULL, checkpoint_ns VARCHAR(500) NOT NULL,
 checkpoint_id VARCHAR(160) NOT NULL, payload JSONB NOT NULL CHECK (octet_length(payload::text)<=12000000),
 PRIMARY KEY(owner_id,session_id,thread_id,checkpoint_ns,checkpoint_id)
);
ALTER TABLE public.agent_graph_checkpoints ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agent_graph_checkpoints FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.agent_graph_checkpoints TO service_role;
CREATE TABLE IF NOT EXISTS public.agent_graph_pending_writes (
 owner_id VARCHAR(160) NOT NULL, session_id VARCHAR(160) NOT NULL,
 thread_id VARCHAR(160) NOT NULL, checkpoint_ns VARCHAR(500) NOT NULL,
 checkpoint_id VARCHAR(160) NOT NULL, payload JSONB NOT NULL CHECK (octet_length(payload::text)<=12000000), task_id VARCHAR(160) NOT NULL, idx INTEGER NOT NULL,
 PRIMARY KEY(owner_id,session_id,thread_id,checkpoint_ns,checkpoint_id,task_id,idx)
);
ALTER TABLE public.agent_graph_pending_writes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agent_graph_pending_writes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.agent_graph_pending_writes TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_graph_checkpoint(
 p_owner TEXT,p_device TEXT,p_origin TEXT,p_session TEXT,p_thread TEXT,p_ns TEXT,
 p_checkpoint TEXT,p_operation TEXT,p_payload JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE w JSONB; result JSONB;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500
 OR p_origin IS NULL OR length(p_origin) NOT BETWEEN 1 AND 16 OR p_session IS NULL OR length(p_session) NOT BETWEEN 1 AND 160
 OR p_thread IS NULL OR length(p_thread) NOT BETWEEN 1 AND 160 OR (p_ns IS NULL AND p_operation<>'read') OR length(p_ns)>500
 OR p_operation IS NULL OR p_operation NOT IN ('read','put','writes','delete')
 OR (p_operation IN ('put','writes') AND (p_checkpoint IS NULL OR length(p_checkpoint) NOT BETWEEN 1 AND 160))
 OR (p_checkpoint IS NOT NULL AND length(p_checkpoint)>160)
 OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>12000000 THEN
 RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_GRAPH_CHECKPOINT'; END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 IF EXISTS(SELECT 1 FROM public.deleted_agent_sessions WHERE owner_id=p_owner AND session_id=p_session) THEN RAISE SQLSTATE 'PT410' USING MESSAGE='SESSION_DELETED'; END IF;
 IF EXISTS(SELECT 1 FROM public.sync_preferences WHERE owner_id=p_owner AND NOT sync_agent_sessions) THEN IF p_operation='read' THEN RETURN '[]'::jsonb; ELSE RETURN 'null'::jsonb; END IF; END IF;
 IF p_operation='read' THEN
 SELECT COALESCE(jsonb_agg(item ORDER BY cid DESC),'[]'::jsonb) INTO result FROM (
 SELECT c.checkpoint_id cid,jsonb_build_object('checkpoint_ns',c.checkpoint_ns,'checkpoint_id',c.checkpoint_id,'payload',c.payload,'writes',
 COALESCE((SELECT jsonb_agg(w.payload||jsonb_build_object('task_id',w.task_id,'idx',w.idx)) FROM public.agent_graph_pending_writes w WHERE
 w.owner_id=c.owner_id AND w.session_id=c.session_id AND w.thread_id=c.thread_id AND w.checkpoint_ns=c.checkpoint_ns AND w.checkpoint_id=c.checkpoint_id),'[]'::jsonb)) item
 FROM public.agent_graph_checkpoints c WHERE c.owner_id=p_owner AND c.session_id=p_session AND c.thread_id=p_thread AND (p_ns IS NULL OR c.checkpoint_ns=p_ns) AND (p_checkpoint IS NULL OR c.checkpoint_id=p_checkpoint)) q;
 RETURN result;
 ELSIF p_operation='put' THEN
 IF (p_payload-ARRAY['checkpoint','metadata','parent'])<>'{}'::jsonb OR NOT(p_payload ?& ARRAY['checkpoint','metadata','parent']) THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_GRAPH_PAYLOAD'; END IF;
 INSERT INTO public.agent_graph_checkpoints VALUES(p_owner,p_session,p_thread,p_ns,p_checkpoint,p_payload)
 ON CONFLICT(owner_id,session_id,thread_id,checkpoint_ns,checkpoint_id) DO UPDATE SET payload=EXCLUDED.payload;
 ELSIF p_operation='writes' THEN
 IF (p_payload-'writes')<>'{}'::jsonb OR jsonb_typeof(p_payload->'writes') IS DISTINCT FROM 'array' THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_GRAPH_WRITES'; END IF;
 FOR w IN SELECT value FROM jsonb_array_elements(p_payload->'writes') LOOP
 IF jsonb_typeof(w)<>'object' OR (w-ARRAY['task_id','idx','channel','task_path','value'])<>'{}'::jsonb
 OR NOT(w ?& ARRAY['task_id','idx','channel','task_path','value'])
 OR length(w->>'task_id')>160 OR length(w->>'channel')>500 OR length(w->>'task_path')>1000
 OR (w->>'idx')::integer < -4 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_GRAPH_WRITE'; END IF;
 INSERT INTO public.agent_graph_pending_writes(owner_id,session_id,thread_id,checkpoint_ns,checkpoint_id,task_id,idx,payload)
 VALUES(p_owner,p_session,p_thread,p_ns,p_checkpoint,w->>'task_id',(w->>'idx')::integer,w-ARRAY['task_id','idx'])
 ON CONFLICT(owner_id,session_id,thread_id,checkpoint_ns,checkpoint_id,task_id,idx) DO UPDATE SET payload=EXCLUDED.payload WHERE EXCLUDED.idx<0;
 END LOOP;
 ELSE
 DELETE FROM public.agent_graph_pending_writes WHERE owner_id=p_owner AND session_id=p_session AND thread_id=p_thread;
 DELETE FROM public.agent_graph_checkpoints WHERE owner_id=p_owner AND session_id=p_session AND thread_id=p_thread;
 END IF;
 RETURN 'null'::jsonb;
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_graph_checkpoint(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_graph_checkpoint(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_delete_session(p_owner TEXT,p_device TEXT,p_session TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160
    OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500
    OR p_session IS NULL OR length(p_session) NOT BETWEEN 1 AND 160 THEN
   RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_SESSION';
 END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner)
   ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 INSERT INTO public.deleted_agent_sessions(owner_id,session_id) VALUES(p_owner,p_session)
   ON CONFLICT(owner_id,session_id) DO NOTHING;
 DELETE FROM public.agent_graph_pending_writes WHERE owner_id=p_owner AND session_id=p_session;
 DELETE FROM public.agent_graph_checkpoints WHERE owner_id=p_owner AND session_id=p_session;
 DELETE FROM public.agent_turns WHERE owner_id=p_owner AND session_id=p_session;
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_delete_session(TEXT,TEXT,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_delete_session(TEXT,TEXT,TEXT) TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_set_preferences(
  p_owner TEXT,p_sync_records BOOLEAN,p_sync_agent_sessions BOOLEAN,p_sync_thumbnails BOOLEAN,p_sync_paths BOOLEAN
) RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $function$
DECLARE previous_paths BOOLEAN; invalidate_paths BOOLEAN;
BEGIN
  INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner)
    ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
  SELECT sync_paths INTO previous_paths FROM public.sync_preferences WHERE owner_id=p_owner;
  invalidate_paths := NOT p_sync_paths AND (previous_paths IS DISTINCT FROM FALSE OR EXISTS(
    SELECT 1 FROM public.sync_events WHERE owner_id=p_owner AND (body_json->'payload')::jsonb ?| ARRAY['source','paths']));
  INSERT INTO public.sync_preferences(owner_id,sync_records,sync_agent_sessions,sync_thumbnails,sync_paths,updated_at)
    VALUES(p_owner,p_sync_records,p_sync_agent_sessions,p_sync_thumbnails,p_sync_paths,CURRENT_TIMESTAMP)
    ON CONFLICT(owner_id) DO UPDATE SET sync_records=EXCLUDED.sync_records,
      sync_agent_sessions=EXCLUDED.sync_agent_sessions,sync_thumbnails=EXCLUDED.sync_thumbnails,
      sync_paths=EXCLUDED.sync_paths,updated_at=EXCLUDED.updated_at;
  IF NOT p_sync_agent_sessions THEN
    DELETE FROM public.agent_graph_pending_writes WHERE owner_id=p_owner;
    DELETE FROM public.agent_graph_checkpoints WHERE owner_id=p_owner;
  END IF;
  IF NOT p_sync_paths THEN
    UPDATE public.sync_events SET body_json=jsonb_set(body_json::jsonb,'{payload}',(body_json->'payload')::jsonb-'source'-'paths')::json
      WHERE owner_id=p_owner;
  END IF;
  IF invalidate_paths THEN
    INSERT INTO public.sync_invalidations(owner_id,kind) VALUES(p_owner,'paths_redacted');
  END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.yingxu_set_preferences(TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_set_preferences(TEXT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(11,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
