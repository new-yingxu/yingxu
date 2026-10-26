-- Local preparation only. Requires v12. Erases sync content, not source files/account.
-- SECURITY DEFINER owner MUST be the trusted database migration owner. No dynamic SQL.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=12) THEN RAISE EXCEPTION 'v12 required'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.data_cleanup_receipts (
 owner_id VARCHAR(160) NOT NULL,
 request_id VARCHAR(160) NOT NULL,
 device_id VARCHAR(500) NOT NULL,
 origin VARCHAR(16) NOT NULL CHECK(origin IN ('desktop','mobile')),
 result_json JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(owner_id,request_id)
);
ALTER TABLE public.data_cleanup_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.data_cleanup_receipts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT ON public.data_cleanup_receipts TO service_role;
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
 ) sessions ON CONFLICT(owner_id,session_id) DO NOTHING;
 GET DIAGNOSTICS s_count=ROW_COUNT;
 DELETE FROM public.sync_events WHERE owner_id=p_owner; GET DIAGNOSTICS e_count=ROW_COUNT;
 DELETE FROM public.agent_turns WHERE owner_id=p_owner; GET DIAGNOSTICS t_count=ROW_COUNT;
 DELETE FROM public.agent_actions WHERE owner_id=p_owner; GET DIAGNOSTICS a_count=ROW_COUNT;
 DELETE FROM public.agent_graph_pending_writes WHERE owner_id=p_owner; GET DIAGNOSTICS w_count=ROW_COUNT;
 DELETE FROM public.agent_graph_checkpoints WHERE owner_id=p_owner; GET DIAGNOSTICS c_count=ROW_COUNT;
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
 '[{"kind": "device_revocations", "reason": "Keep device IDs/origins and revocation state so old sessions cannot republish; display names are erased."}, {"kind": "model_request_deduplication", "reason": "Keep request/session/turn/device IDs, query hashes, attempt IDs, states and timestamps; do not repeat billed or uncertain calls."}, {"kind": "session_tombstones", "reason": "Keep session IDs and deletion timestamps to block recreation of erased agent history."}, {"kind": "empty_preset_tombstones", "reason": "Keep preset IDs/versions and empty deleted revisions so clients can remove stale local copies."}, {"kind": "cleanup_receipts_and_owner_lock", "reason": "Keep request/device/origin IDs, counts and fixed explanations for idempotency and serialized revocation."}, {"kind": "disabled_preferences_and_invalidation", "reason": "Keep all sync flags disabled and one deletion notification; a new login must explicitly enable sync."}]'::jsonb);
 INSERT INTO public.data_cleanup_receipts(owner_id,request_id,device_id,origin,result_json) VALUES(p_owner,p_request,p_device,p_origin,result);
 RETURN result;
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_clear_sync_data(TEXT,TEXT,TEXT,TEXT,BOOLEAN) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_clear_sync_data(TEXT,TEXT,TEXT,TEXT,BOOLEAN) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(13,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
