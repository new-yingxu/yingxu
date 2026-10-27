-- Local preparation only. Requires v15. Serialize proposal insertion with cleanup.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=15) THEN RAISE EXCEPTION 'v15 required'; END IF; END $$;
CREATE OR REPLACE FUNCTION public.yingxu_add_action(p_owner TEXT,p_device TEXT,p_origin TEXT,p_action JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE expires TIMESTAMPTZ;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR length(btrim(p_owner))=0 OR p_owner ~ '[[:cntrl:]]'
 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500 OR length(btrim(p_device))=0 OR p_device ~ '[[:cntrl:]]'
 OR p_origin IS NULL OR p_origin NOT IN ('desktop','mobile')
 OR p_action IS NULL OR jsonb_typeof(p_action)<>'object' OR octet_length(p_action::text)>2200000
 OR NOT(p_action ?& ARRAY['id','tool','state','proposal_json','expires_at'])
 OR (p_action-ARRAY['id','tool','state','proposal_json','expires_at'])<>'{}'::jsonb
 OR jsonb_typeof(p_action->'id') IS DISTINCT FROM 'string' OR length(p_action->>'id') NOT BETWEEN 1 AND 500 OR length(btrim(p_action->>'id'))=0 OR (p_action->>'id') ~ '[[:cntrl:]]'
 OR jsonb_typeof(p_action->'tool') IS DISTINCT FROM 'string' OR length(p_action->>'tool') NOT BETWEEN 1 AND 64 OR length(btrim(p_action->>'tool'))=0 OR (p_action->>'tool') ~ '[[:cntrl:]]'
 OR jsonb_typeof(p_action->'state') IS DISTINCT FROM 'string' OR (p_action->>'state') NOT IN ('proposed','awaiting_local_confirmation')
 OR jsonb_typeof(p_action->'proposal_json') IS DISTINCT FROM 'object' OR octet_length((p_action->'proposal_json')::text)>2097152
 OR jsonb_typeof(p_action->'expires_at') IS DISTINCT FROM 'string' OR length(p_action->>'expires_at') NOT BETWEEN 1 AND 100
 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_INITIAL_ACTION'; END IF;
 BEGIN
 expires:=(p_action->>'expires_at')::timestamptz;
 IF NOT isfinite(expires) THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_ACTION_EXPIRY'; END IF;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_ACTION_EXPIRY';
 END;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 BEGIN
 INSERT INTO public.agent_actions(id,owner_id,device_id,origin,tool,state,proposal_json,created_at,expires_at,state_updated_at)
 VALUES(p_action->>'id',p_owner,p_device,p_origin,p_action->>'tool',p_action->>'state',(p_action->'proposal_json')::json,CURRENT_TIMESTAMP,expires,CURRENT_TIMESTAMP);
 EXCEPTION WHEN unique_violation THEN RAISE SQLSTATE 'PT409' USING MESSAGE='ACTION_ID_CONFLICT';
 END;
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_add_action(TEXT,TEXT,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_add_action(TEXT,TEXT,TEXT,JSONB) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(16,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
