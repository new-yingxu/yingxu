-- Applied to CloudBase 2026-10-26 via management SQL. Requires v9. New application not deployed.
BEGIN;
CREATE TABLE IF NOT EXISTS public.agent_model_requests (
 owner_id VARCHAR(160) NOT NULL,
 session_id VARCHAR(160) NOT NULL,
 turn_id VARCHAR(160) NOT NULL,
 device_id VARCHAR(500) NOT NULL,
 request_hash VARCHAR(64) NOT NULL,
 attempt_id VARCHAR(36) NOT NULL,
 state VARCHAR(30) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(owner_id,session_id,turn_id)
);
ALTER TABLE public.agent_model_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.agent_model_requests FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.agent_model_requests TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_reserve_model(p_owner TEXT,p_device TEXT,p_session TEXT,p_turn TEXT,p_query TEXT,p_attempt TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE prior public.agent_model_requests%ROWTYPE; history public.agent_turns%ROWTYPE; h TEXT;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500
 OR p_session IS NULL OR length(p_session) NOT BETWEEN 1 AND 160 OR p_turn IS NULL OR length(p_turn) NOT BETWEEN 1 AND 160
 OR p_query IS NULL OR length(p_query) NOT BETWEEN 1 AND 500 OR p_attempt IS NULL OR length(p_attempt)<>36 THEN
 RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_MODEL_REQUEST'; END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 IF EXISTS(SELECT 1 FROM public.deleted_agent_sessions WHERE owner_id=p_owner AND session_id=p_session) THEN RAISE SQLSTATE 'PT410' USING MESSAGE='SESSION_DELETED'; END IF;
 h:=encode(sha256(convert_to(p_query,'UTF8')),'hex');
 SELECT * INTO prior FROM public.agent_model_requests WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
 IF FOUND THEN
 IF prior.device_id IS DISTINCT FROM p_device OR prior.request_hash IS DISTINCT FROM h THEN RAISE SQLSTATE 'PT409' USING MESSAGE='TURN_ID_CONFLICT'; END IF;
 SELECT * INTO history FROM public.agent_turns WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
 IF prior.state='completed' AND FOUND THEN RETURN jsonb_build_object('state','completed','response',history.response_json::jsonb,'persisted',TRUE,'duplicate',TRUE); END IF;
 RETURN jsonb_build_object('state',prior.state);
 END IF;
 IF EXISTS(SELECT 1 FROM public.agent_turns WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn) THEN RAISE SQLSTATE 'PT409' USING MESSAGE='TURN_ID_CONFLICT'; END IF;
 INSERT INTO public.agent_model_requests(owner_id,session_id,turn_id,device_id,request_hash,attempt_id,state) VALUES(p_owner,p_session,p_turn,p_device,h,p_attempt,'pending');
 RETURN jsonb_build_object('state','reserved','attemptId',p_attempt);
END;
$fn$;
CREATE OR REPLACE FUNCTION public.yingxu_finish_model(p_owner TEXT,p_device TEXT,p_session TEXT,p_turn TEXT,p_attempt TEXT,p_query TEXT,p_response JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE req public.agent_model_requests%ROWTYPE; enabled BOOLEAN; final_state TEXT;
BEGIN
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 SELECT * INTO req FROM public.agent_model_requests WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
 IF NOT FOUND OR req.device_id IS DISTINCT FROM p_device OR req.attempt_id IS DISTINCT FROM p_attempt OR req.state<>'pending'
 OR req.request_hash IS DISTINCT FROM encode(sha256(convert_to(p_query,'UTF8')),'hex') THEN RAISE SQLSTATE 'PT409' USING MESSAGE='TURN_ID_CONFLICT'; END IF;
 IF p_response IS NULL THEN
 UPDATE public.agent_model_requests SET state='uncertain' WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
 RETURN jsonb_build_object('state','uncertain'); END IF;
 IF jsonb_typeof(p_response) IS DISTINCT FROM 'object' THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_MODEL_RESPONSE'; END IF;
 IF EXISTS(SELECT 1 FROM public.deleted_agent_sessions WHERE owner_id=p_owner AND session_id=p_session) THEN RAISE SQLSTATE 'PT410' USING MESSAGE='SESSION_DELETED'; END IF;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 SELECT sync_agent_sessions INTO enabled FROM public.sync_preferences WHERE owner_id=p_owner;
 IF enabled IS DISTINCT FROM FALSE THEN
 INSERT INTO public.agent_turns(owner_id,session_id,turn_id,device_id,query,response_json) VALUES(p_owner,p_session,p_turn,p_device,p_query,p_response::json);
 final_state:='completed'; ELSE final_state:='completed_unretained'; END IF;
 UPDATE public.agent_model_requests SET state=final_state WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
 RETURN jsonb_build_object('state',final_state,'response',p_response,'persisted',enabled IS DISTINCT FROM FALSE,'duplicate',FALSE);
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_reserve_model(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT), public.yingxu_finish_model(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_reserve_model(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT), public.yingxu_finish_model(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_save_turn(p_owner TEXT,p_device TEXT,p_session TEXT,p_turn TEXT,p_query TEXT,p_response JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE prior public.agent_turns%ROWTYPE; enabled BOOLEAN;
BEGIN
  IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160
     OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500
     OR p_session IS NULL OR length(p_session) NOT BETWEEN 1 AND 160
     OR p_turn IS NULL OR length(p_turn) NOT BETWEEN 1 AND 160
     OR p_query IS NULL OR length(p_query) NOT BETWEEN 1 AND 500
     OR jsonb_typeof(p_response) IS DISTINCT FROM 'object' THEN
    RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_AGENT_TURN';
  END IF;
  INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner)
    ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
  PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND NOT revoked FOR SHARE;
  IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
  IF EXISTS(SELECT 1 FROM public.deleted_agent_sessions WHERE owner_id=p_owner AND session_id=p_session) THEN
    RAISE SQLSTATE 'PT410' USING MESSAGE='SESSION_DELETED';
  END IF;
  IF EXISTS(SELECT 1 FROM public.agent_model_requests WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn) THEN RAISE SQLSTATE 'PT409' USING MESSAGE='TURN_ID_CONFLICT'; END IF;
  SELECT * INTO prior FROM public.agent_turns WHERE owner_id=p_owner AND session_id=p_session AND turn_id=p_turn;
  IF FOUND THEN
    IF prior.device_id IS DISTINCT FROM p_device OR prior.query IS DISTINCT FROM p_query THEN
      RAISE SQLSTATE 'PT409' USING MESSAGE='TURN_ID_CONFLICT';
    END IF;
    RETURN jsonb_build_object('response',prior.response_json::jsonb,'persisted',TRUE,'duplicate',TRUE);
  END IF;
  SELECT sync_agent_sessions INTO enabled FROM public.sync_preferences WHERE owner_id=p_owner;
  IF enabled IS FALSE THEN
    RETURN jsonb_build_object('response',p_response,'persisted',FALSE,'duplicate',FALSE);
  END IF;
  INSERT INTO public.agent_turns(owner_id,session_id,turn_id,device_id,query,response_json)
    VALUES(p_owner,p_session,p_turn,p_device,p_query,p_response::json);
  RETURN jsonb_build_object('response',p_response,'persisted',TRUE,'duplicate',FALSE);
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_save_turn(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_save_turn(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(10,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
