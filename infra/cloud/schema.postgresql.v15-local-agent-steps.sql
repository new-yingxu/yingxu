-- Local preparation only. Requires v14. Durable desktop-local tool handoff.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=14) THEN RAISE EXCEPTION 'v14 required'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.local_agent_steps (
 owner_id VARCHAR(160) NOT NULL,session_id VARCHAR(160) NOT NULL,turn_id VARCHAR(160) NOT NULL,
 step_index INTEGER NOT NULL CHECK(step_index BETWEEN 0 AND 2),
 device_id VARCHAR(500) NOT NULL,origin VARCHAR(16) NOT NULL CHECK(origin='desktop'),
 root_hash VARCHAR(64) NOT NULL CHECK(root_hash ~ '^[0-9a-f]{64}$'),
 input_hash VARCHAR(64) NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 attempt_id VARCHAR(36) NOT NULL CHECK(attempt_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
 output_nonce VARCHAR(36) CHECK(output_nonce IS NULL OR output_nonce ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
 state VARCHAR(30) NOT NULL CHECK(state IN ('pending','awaiting_tools','completed','uncertain')),
 response_json JSONB CHECK(response_json IS NULL OR (jsonb_typeof(response_json)='object' AND octet_length(response_json::text)<=2097152)),
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(owner_id,session_id,turn_id,step_index),
 CHECK((state='awaiting_tools' AND output_nonce IS NOT NULL) OR (state<>'awaiting_tools' AND output_nonce IS NULL)),
 CHECK(state NOT IN ('pending','uncertain') OR response_json IS NULL)
);
ALTER TABLE public.local_agent_steps ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.local_agent_steps FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.local_agent_steps TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_local_agent_step(
 p_owner TEXT,p_device TEXT,p_origin TEXT,p_operation TEXT,p_session TEXT,p_turn TEXT,p_step INTEGER,p_payload JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE prior public.local_agent_steps%ROWTYPE; parent public.local_agent_steps%ROWTYPE;
 retain BOOLEAN; attempt TEXT; next_state TEXT; nonce TEXT; response JSONB; bad_keys BOOLEAN;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR length(btrim(p_owner))=0 OR p_owner ~ '[[:cntrl:]]'
 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500 OR length(btrim(p_device))=0 OR p_device ~ '[[:cntrl:]]'
 OR p_origin IS DISTINCT FROM 'desktop' OR p_session IS NULL OR length(p_session) NOT BETWEEN 1 AND 160 OR length(btrim(p_session))=0 OR p_session ~ '[[:cntrl:]]'
 OR p_turn IS NULL OR length(p_turn) NOT BETWEEN 1 AND 160 OR length(btrim(p_turn))=0 OR p_turn ~ '[[:cntrl:]]'
 OR p_step IS NULL OR p_step NOT BETWEEN 0 AND 2 OR p_operation IS NULL OR p_operation NOT IN ('reserve','finish')
 OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>2200000 THEN
 RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_LOCAL_STEP'; END IF;
 IF p_operation='reserve' THEN
 IF NOT(p_payload ?& ARRAY['root_hash','input_hash','parent_nonce']) OR (p_payload-ARRAY['root_hash','input_hash','parent_nonce'])<>'{}'::jsonb
 OR jsonb_typeof(p_payload->'root_hash') IS DISTINCT FROM 'string' OR jsonb_typeof(p_payload->'input_hash') IS DISTINCT FROM 'string'
 OR (p_payload->>'root_hash') IS NULL OR (p_payload->>'root_hash') !~ '^[0-9a-f]{64}$'
 OR (p_payload->>'input_hash') IS NULL OR (p_payload->>'input_hash') !~ '^[0-9a-f]{64}$'
 OR (p_step=0 AND (p_payload->>'parent_nonce') IS NOT NULL)
 OR (p_step>0 AND ((p_payload->>'parent_nonce') IS NULL OR (p_payload->>'parent_nonce') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'))
 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_LOCAL_STEP_RESERVATION'; END IF;
 ELSE
 next_state:=p_payload->>'state';nonce:=p_payload->>'output_nonce';response:=p_payload->'response';
 IF NOT(p_payload ?& ARRAY['attempt_id','state','response','output_nonce']) OR (p_payload-ARRAY['attempt_id','state','response','output_nonce'])<>'{}'::jsonb
 OR (p_payload->>'attempt_id') IS NULL OR (p_payload->>'attempt_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 OR next_state IS NULL OR next_state NOT IN ('awaiting_tools','completed','uncertain')
 OR (next_state='awaiting_tools' AND (nonce IS NULL OR nonce !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'))
 OR (next_state<>'awaiting_tools' AND nonce IS NOT NULL)
 OR (next_state<>'uncertain' AND jsonb_typeof(response) NOT IN ('object','null'))
 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_LOCAL_STEP_RESULT'; END IF;
 IF next_state='uncertain' OR response='null'::jsonb THEN response:=NULL; END IF;
 IF response IS NOT NULL THEN
 IF octet_length(response::text)>2097152 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='LOCAL_STEP_RESPONSE_TOO_LARGE'; END IF;
 WITH RECURSIVE nodes(value) AS (
 SELECT response UNION ALL
 SELECT child.value FROM nodes n CROSS JOIN LATERAL (
 SELECT value FROM jsonb_each(CASE WHEN jsonb_typeof(n.value)='object' THEN n.value ELSE '{}'::jsonb END)
 UNION ALL SELECT value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(n.value)='array' THEN n.value ELSE '[]'::jsonb END)
 ) child
 ) SELECT EXISTS(SELECT 1 FROM nodes n CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(n.value)='object' THEN n.value ELSE '{}'::jsonb END) k(key)
 WHERE replace(lower(k.key),'-','_') IN ('apikey','api_key','authorization','password','credentials','secret','token','access_token','refresh_token','id_token','service_role_key','accesstoken','refreshtoken','idtoken','servicerolekey','client_secret','clientsecret')) INTO bad_keys;
 IF bad_keys THEN RAISE SQLSTATE 'PT422' USING MESSAGE='CREDENTIALS_FORBIDDEN_IN_LOCAL_STEP'; END IF;
 END IF;
 END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin='desktop' AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 IF EXISTS(SELECT 1 FROM public.deleted_agent_sessions WHERE owner_id=p_owner AND session_id=p_session) THEN RAISE SQLSTATE 'PT410' USING MESSAGE='SESSION_DELETED'; END IF;
 SELECT NOT EXISTS(SELECT 1 FROM public.sync_preferences WHERE owner_id=p_owner AND NOT sync_agent_sessions) INTO retain;
 IF p_operation='reserve' THEN
 IF p_step>0 THEN
 SELECT * INTO parent FROM public.local_agent_steps WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn AND step_index=p_step-1;
 IF NOT FOUND OR parent.device_id IS DISTINCT FROM p_device OR parent.origin IS DISTINCT FROM p_origin OR parent.root_hash IS DISTINCT FROM (p_payload->>'root_hash')
 OR parent.state IS DISTINCT FROM 'awaiting_tools' OR parent.output_nonce IS DISTINCT FROM (p_payload->>'parent_nonce') THEN
 RAISE SQLSTATE 'PT409' USING MESSAGE='LOCAL_STEP_PARENT_CONFLICT'; END IF;
 END IF;
 SELECT * INTO prior FROM public.local_agent_steps WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn AND step_index=p_step;
 IF FOUND THEN
 IF prior.device_id IS DISTINCT FROM p_device OR prior.origin IS DISTINCT FROM p_origin OR prior.root_hash IS DISTINCT FROM (p_payload->>'root_hash') OR prior.input_hash IS DISTINCT FROM (p_payload->>'input_hash') THEN
 RAISE SQLSTATE 'PT409' USING MESSAGE='LOCAL_STEP_CONFLICT'; END IF;
 IF retain AND prior.response_json IS NOT NULL THEN RETURN jsonb_build_object('state','replayed','response',prior.response_json); END IF;
 RETURN jsonb_build_object('state',prior.state,'retained',FALSE);
 END IF;
 attempt:=gen_random_uuid()::text;
 INSERT INTO public.local_agent_steps(owner_id,session_id,turn_id,step_index,device_id,origin,root_hash,input_hash,attempt_id,state)
 VALUES(p_owner,p_session,p_turn,p_step,p_device,p_origin,p_payload->>'root_hash',p_payload->>'input_hash',attempt,'pending');
 RETURN jsonb_build_object('state','reserved','attemptId',attempt);
 END IF;
 SELECT * INTO prior FROM public.local_agent_steps WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn AND step_index=p_step;
 IF NOT FOUND OR prior.device_id IS DISTINCT FROM p_device OR prior.origin IS DISTINCT FROM p_origin OR prior.state IS DISTINCT FROM 'pending' OR prior.attempt_id IS DISTINCT FROM (p_payload->>'attempt_id') THEN
 RAISE SQLSTATE 'PT409' USING MESSAGE='LOCAL_STEP_FINISH_CONFLICT'; END IF;
 UPDATE public.local_agent_steps SET state=next_state,output_nonce=nonce,response_json=CASE WHEN retain THEN response ELSE NULL END
 WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn AND step_index=p_step;
 RETURN jsonb_build_object('state',next_state,'retained',retain AND response IS NOT NULL);
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_local_agent_step(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_local_agent_step(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER,JSONB) TO service_role;
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
 UPDATE public.local_agent_steps SET response_json=NULL WHERE owner_id=p_owner AND session_id=p_session;
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
    UPDATE public.local_agent_steps SET response_json=NULL WHERE owner_id=p_owner;
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
CREATE OR REPLACE FUNCTION public.yingxu_clear_sync_data(p_owner TEXT,p_device TEXT,p_origin TEXT,p_request TEXT,p_confirmed BOOLEAN)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $fn$
DECLARE prior public.data_cleanup_receipts%ROWTYPE; result JSONB; active_presets JSONB; item JSONB;
 s_count BIGINT; e_count BIGINT; t_count BIGINT; a_count BIGINT; w_count BIGINT; c_count BIGINT;
 m_count BIGINT; p_count BIGINT; i_count BIGINT; d_count BIGINT;
BEGIN
 IF p_confirmed IS DISTINCT FROM TRUE OR p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR length(btrim(p_owner))=0 OR p_owner ~ '[[:cntrl:]]'
 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500 OR length(btrim(p_device))=0 OR p_device ~ '[[:cntrl:]]'
 OR p_origin IS NULL OR p_origin NOT IN ('desktop','mobile')
 OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 160 OR length(btrim(p_request))=0 OR p_request ~ '[[:cntrl:]]'
 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_CLEANUP_REQUEST'; END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 SELECT * INTO prior FROM public.data_cleanup_receipts WHERE owner_id=p_owner AND request_id=p_request;
 IF FOUND THEN
 IF prior.device_id IS DISTINCT FROM p_device OR prior.origin IS DISTINCT FROM p_origin THEN RAISE SQLSTATE 'PT409' USING MESSAGE='CLEANUP_REQUEST_CONFLICT'; END IF;
 RETURN prior.result_json;
 END IF;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 INSERT INTO public.deleted_agent_sessions(owner_id,session_id)
 SELECT p_owner,session_id FROM (
 SELECT session_id FROM public.agent_turns WHERE owner_id=p_owner
 UNION SELECT session_id FROM public.agent_model_requests WHERE owner_id=p_owner
 UNION SELECT session_id FROM public.agent_graph_checkpoints WHERE owner_id=p_owner
 UNION SELECT session_id FROM public.agent_graph_pending_writes WHERE owner_id=p_owner
 UNION SELECT session_id FROM public.local_agent_steps WHERE owner_id=p_owner
 ) sessions ON CONFLICT(owner_id,session_id) DO NOTHING;
 GET DIAGNOSTICS s_count=ROW_COUNT;
 DELETE FROM public.sync_events WHERE owner_id=p_owner; GET DIAGNOSTICS e_count=ROW_COUNT;
 DELETE FROM public.agent_turns WHERE owner_id=p_owner; GET DIAGNOSTICS t_count=ROW_COUNT;
 DELETE FROM public.agent_actions WHERE owner_id=p_owner; GET DIAGNOSTICS a_count=ROW_COUNT;
 DELETE FROM public.agent_graph_pending_writes WHERE owner_id=p_owner; GET DIAGNOSTICS w_count=ROW_COUNT;
 DELETE FROM public.agent_graph_checkpoints WHERE owner_id=p_owner; GET DIAGNOSTICS c_count=ROW_COUNT;
 UPDATE public.local_agent_steps SET response_json=NULL WHERE owner_id=p_owner;
 IF to_regclass('public.sync_snapshot_leases') IS NOT NULL THEN
 DELETE FROM public.sync_snapshot_leases WHERE owner_id=p_owner;
 END IF;
 SELECT count(*) INTO m_count FROM public.agent_model_requests WHERE owner_id=p_owner;
 UPDATE public.agent_model_requests SET state='completed_unretained' WHERE owner_id=p_owner AND state='completed';
 SELECT COALESCE(jsonb_agg(jsonb_build_object('preset',preset_id,'version',version) ORDER BY preset_id),'[]'::jsonb) INTO active_presets
 FROM (SELECT DISTINCT ON (preset_id) preset_id,version,deleted FROM public.preset_revisions WHERE owner_id=p_owner ORDER BY preset_id,version DESC) latest WHERE NOT deleted;
 UPDATE public.preset_revisions SET body_json='{}'::jsonb,deleted=TRUE WHERE owner_id=p_owner;
 GET DIAGNOSTICS p_count=ROW_COUNT;
 FOR item IN SELECT value FROM jsonb_array_elements(active_presets) LOOP
 INSERT INTO public.preset_revisions(owner_id,preset_id,version,body_json,deleted) VALUES(p_owner,item->>'preset',(item->>'version')::integer+1,'{}'::jsonb,TRUE);
 END LOOP;
 DELETE FROM public.sync_invalidations WHERE owner_id=p_owner; GET DIAGNOSTICS i_count=ROW_COUNT;
 INSERT INTO public.sync_invalidations(owner_id,kind) VALUES(p_owner,'all_sync_data_deleted');
 SELECT count(*) INTO d_count FROM public.devices WHERE owner_id=p_owner AND NOT revoked;
 UPDATE public.devices SET revoked=TRUE,display_name='' WHERE owner_id=p_owner;
 INSERT INTO public.sync_preferences(owner_id,sync_records,sync_agent_sessions,sync_thumbnails,sync_paths,updated_at)
 VALUES(p_owner,FALSE,FALSE,FALSE,FALSE,CURRENT_TIMESTAMP)
 ON CONFLICT(owner_id) DO UPDATE SET sync_records=FALSE,sync_agent_sessions=FALSE,sync_thumbnails=FALSE,sync_paths=FALSE,updated_at=CURRENT_TIMESTAMP;
 result:=jsonb_build_object('cleared',TRUE,'counts',jsonb_build_object(
 'sessionTombstonesAdded',s_count,'syncEvents',e_count,'agentTurns',t_count,'agentActions',a_count,
 'graphPendingWrites',w_count,'graphCheckpoints',c_count,'modelRequestsRetained',m_count,
 'presetRevisionsCleared',p_count,'presetTombstonesAdded',jsonb_array_length(active_presets),
 'previousInvalidationsRemoved',i_count),'devicesRevoked',d_count,'retainedSecurityMetadata',
 '[{"kind": "device_revocations", "reason": "Keep device IDs/origins and revocation state so old sessions cannot republish; display names are erased."}, {"kind": "model_request_deduplication", "reason": "Keep request/session/turn/device IDs, query hashes, attempt IDs, states and timestamps; do not repeat billed or uncertain calls."}, {"kind": "session_tombstones", "reason": "Keep session IDs and deletion timestamps to block recreation of erased agent history."}, {"kind": "empty_preset_tombstones", "reason": "Keep preset IDs/versions and empty deleted revisions so clients can remove stale local copies."}, {"kind": "cleanup_receipts_and_owner_lock", "reason": "Keep request/device/origin IDs, counts and fixed explanations for idempotency and serialized revocation."}, {"kind": "disabled_preferences_and_invalidation", "reason": "Keep all sync flags disabled and one deletion notification; a new login must explicitly enable sync."}, {"kind": "local_step_deduplication", "reason": "Keep local-step session/turn/device IDs, root/input hashes, attempt IDs, nonces, states and timestamps; clear cached responses and never automatically repeat pending or uncertain model calls."}]'::jsonb);
 INSERT INTO public.data_cleanup_receipts(owner_id,request_id,device_id,origin,result_json) VALUES(p_owner,p_request,p_device,p_origin,result);
 RETURN result;
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_clear_sync_data(TEXT,TEXT,TEXT,TEXT,BOOLEAN) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_clear_sync_data(TEXT,TEXT,TEXT,TEXT,BOOLEAN) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(15,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
