-- Local preparation only. Append-only name presets; requires v11.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=11) THEN RAISE EXCEPTION 'v11 required'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.preset_revisions (
 id SERIAL PRIMARY KEY,
 owner_id VARCHAR(160) NOT NULL,
 preset_id VARCHAR(160) NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 body_json JSONB NOT NULL CHECK (
   (deleted AND body_json='{}'::jsonb) OR (NOT deleted AND jsonb_typeof(body_json)='object' AND body_json ?& ARRAY['name','pattern','isDefault']
   AND (body_json-ARRAY['name','pattern','isDefault'])='{}'::jsonb
   AND jsonb_typeof(body_json->'name')='string' AND length(body_json->>'name') BETWEEN 1 AND 120
   AND length(btrim(body_json->>'name'))>0
   AND jsonb_typeof(body_json->'pattern')='string' AND length(body_json->>'pattern') BETWEEN 1 AND 500
   AND length(btrim(body_json->>'pattern'))>0
   AND jsonb_typeof(body_json->'isDefault')='boolean')),
 deleted BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT uq_preset_revision UNIQUE(owner_id,preset_id,version)
);
CREATE INDEX IF NOT EXISTS preset_revisions_owner_feed ON public.preset_revisions(owner_id,id);
ALTER TABLE public.preset_revisions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.preset_revisions FROM PUBLIC,anon,authenticated;
REVOKE ALL ON SEQUENCE public.preset_revisions_id_seq FROM PUBLIC,anon,authenticated;
-- Revisions append normally; confirmed deletion scrubs prior bodies and appends a tombstone.
REVOKE DELETE,TRUNCATE ON public.preset_revisions FROM service_role;
GRANT SELECT,INSERT,UPDATE ON public.preset_revisions TO service_role;
GRANT USAGE,SELECT ON SEQUENCE public.preset_revisions_id_seq TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_presets(
 p_owner TEXT,p_device TEXT,p_origin TEXT,p_operation TEXT,
 p_preset TEXT DEFAULT NULL,p_body JSONB DEFAULT NULL,p_expected INTEGER DEFAULT NULL,
 p_confirmed BOOLEAN DEFAULT FALSE,p_after BIGINT DEFAULT 0,p_limit INTEGER DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE prior public.preset_revisions%ROWTYPE; item public.preset_revisions%ROWTYPE;
 target public.preset_revisions%ROWTYPE; has_prior BOOLEAN; active_count INTEGER; result JSONB;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR length(btrim(p_owner))=0 OR p_owner ~ '[[:cntrl:]]'
 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500 OR length(btrim(p_device))=0 OR p_device ~ '[[:cntrl:]]'
 OR p_origin IS NULL OR p_origin NOT IN ('desktop','mobile')
 OR p_operation IS NULL OR p_operation NOT IN ('changes','put','delete') THEN
 RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_PRESET_REQUEST'; END IF;
 IF p_operation='changes' THEN
 IF p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_PRESET_CURSOR'; END IF;
 ELSE
 IF p_preset IS NULL OR length(p_preset) NOT BETWEEN 1 AND 160 OR length(btrim(p_preset))=0 OR p_preset ~ '[[:cntrl:]]'
 OR p_expected IS NULL OR p_expected<0 OR p_expected>=2147483647 THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_PRESET_VERSION'; END IF;
 END IF;
 IF p_operation='put' AND (p_body IS NULL OR jsonb_typeof(p_body)<>'object'
 OR NOT(p_body ?& ARRAY['name','pattern','isDefault']) OR (p_body-ARRAY['name','pattern','isDefault'])<>'{}'::jsonb
 OR jsonb_typeof(p_body->'name') IS DISTINCT FROM 'string' OR length(p_body->>'name') NOT BETWEEN 1 AND 120 OR length(btrim(p_body->>'name'))=0
 OR jsonb_typeof(p_body->'pattern') IS DISTINCT FROM 'string' OR length(p_body->>'pattern') NOT BETWEEN 1 AND 500 OR length(btrim(p_body->>'pattern'))=0
 OR jsonb_typeof(p_body->'isDefault') IS DISTINCT FROM 'boolean') THEN RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_PRESET_BODY'; END IF;
 IF p_operation='delete' AND p_confirmed IS DISTINCT FROM TRUE THEN RAISE SQLSTATE 'PT422' USING MESSAGE='PRESET_CONFIRMATION_REQUIRED'; END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 IF p_operation='changes' THEN
 SELECT jsonb_build_object('changes',COALESCE(jsonb_agg(jsonb_build_object('id',id,'presetId',preset_id,'version',version,'body',body_json,'deleted',deleted,'createdAt',created_at) ORDER BY id) FILTER(WHERE seq<=p_limit),'[]'::jsonb),
 'nextCursor',COALESCE(max(id) FILTER(WHERE seq<=p_limit),p_after),'hasMore',count(*)>p_limit)
 INTO result FROM (SELECT r.*,row_number() OVER(ORDER BY id) seq FROM public.preset_revisions r WHERE owner_id=p_owner AND id>p_after ORDER BY id LIMIT p_limit+1) page;
 RETURN result;
 END IF;
 IF EXISTS(SELECT 1 FROM public.sync_preferences WHERE owner_id=p_owner AND NOT sync_records) THEN RETURN jsonb_build_object('error','PRESET_SYNC_DISABLED'); END IF;
 SELECT * INTO prior FROM public.preset_revisions WHERE owner_id=p_owner AND preset_id=p_preset ORDER BY version DESC LIMIT 1;
 has_prior:=FOUND;
 IF p_operation='delete' THEN
 IF NOT has_prior THEN RETURN jsonb_build_object('error','PRESET_NOT_FOUND'); END IF;
 IF prior.deleted AND prior.version=p_expected+1 THEN target:=prior;
 ELSIF prior.deleted OR prior.version<>p_expected THEN RETURN jsonb_build_object('error','PRESET_CONFLICT');
 ELSE
 UPDATE public.preset_revisions SET body_json='{}'::jsonb,deleted=TRUE WHERE owner_id=p_owner AND preset_id=p_preset;
 INSERT INTO public.preset_revisions(owner_id,preset_id,version,body_json,deleted) VALUES(p_owner,p_preset,p_expected+1,'{}'::jsonb,TRUE) RETURNING * INTO target;
 END IF;
 ELSE
 IF has_prior AND prior.version=p_expected+1 AND NOT prior.deleted AND prior.body_json=p_body THEN target:=prior;
 ELSE
 IF COALESCE(prior.version,0)<>p_expected THEN RETURN jsonb_build_object('error','PRESET_CONFLICT'); END IF;
 SELECT count(*) INTO active_count FROM (SELECT DISTINCT ON (preset_id) deleted FROM public.preset_revisions WHERE owner_id=p_owner ORDER BY preset_id,version DESC) latest WHERE NOT deleted;
 IF (NOT has_prior OR prior.deleted) AND active_count>=100 THEN RETURN jsonb_build_object('error','PRESET_LIMIT'); END IF;
 IF (p_body->>'isDefault')::boolean THEN
 FOR item IN SELECT * FROM (SELECT DISTINCT ON (preset_id) * FROM public.preset_revisions WHERE owner_id=p_owner ORDER BY preset_id,version DESC) latest
 WHERE NOT deleted AND preset_id<>p_preset AND (body_json->>'isDefault')::boolean ORDER BY id LOOP
 INSERT INTO public.preset_revisions(owner_id,preset_id,version,body_json,deleted) VALUES(p_owner,item.preset_id,item.version+1,item.body_json||'{"isDefault":false}'::jsonb,FALSE);
 END LOOP;
 END IF;
 INSERT INTO public.preset_revisions(owner_id,preset_id,version,body_json,deleted) VALUES(p_owner,p_preset,p_expected+1,p_body,FALSE) RETURNING * INTO target;
 END IF;
 END IF;
 RETURN jsonb_build_object('revision',jsonb_build_object('id',target.id,'presetId',target.preset_id,'version',target.version,'body',target.body_json,'deleted',target.deleted,'createdAt',target.created_at),
 'duplicate',has_prior AND target.id=prior.id);
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_presets(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,BOOLEAN,BIGINT,INTEGER) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_presets(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,BOOLEAN,BIGINT,INTEGER) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(12,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
