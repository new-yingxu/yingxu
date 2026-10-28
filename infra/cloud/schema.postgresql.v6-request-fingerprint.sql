-- Applied to CloudBase 2026-10-26 via management SQL; application not deployed. Requires migration 5.
-- PostgreSQL direct SQL and RPC share this canonicalization; SQLite hashes differ.
-- Previously redacted historical requests cannot be reconstructed: conflicting
-- full-path replays of those rows continue to fail closed.
BEGIN;
ALTER TABLE public.sync_events ADD COLUMN IF NOT EXISTS request_hash VARCHAR(64);
UPDATE public.sync_events
  SET request_hash=encode(sha256(convert_to(body_json::jsonb::text,'UTF8')),'hex')
  WHERE request_hash IS NULL;
CREATE OR REPLACE FUNCTION public.yingxu_ingest_batch(p_owner TEXT, p_device TEXT, p_events JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $function$
DECLARE
  item JSONB;
  prior public.sync_events%ROWTYPE;
  pref public.sync_preferences%ROWTYPE;
  accepted JSONB := '[]'::jsonb;
  duplicates JSONB := '[]'::jsonb;
BEGIN
  IF p_owner IS NULL OR p_device IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160
     OR length(p_device) NOT BETWEEN 1 AND 500 OR jsonb_typeof(p_events) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_events) NOT BETWEEN 1 AND 100 THEN
    RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_SYNC_BATCH';
  END IF;
  -- Acquired before allocating event IDs, held through transaction commit.
  INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner)
    ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
  PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND NOT revoked FOR SHARE;
  IF NOT FOUND THEN
    RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED';
  END IF;
  SELECT * INTO pref FROM public.sync_preferences WHERE owner_id=p_owner;
  FOR item IN SELECT value FROM jsonb_array_elements(p_events) LOOP
    IF item->>'deviceId' IS DISTINCT FROM p_device OR item->>'eventId' IS NULL OR item->>'sequence' IS NULL THEN
      RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_SYNC_EVENT';
    END IF;
    SELECT * INTO prior FROM public.sync_events
      WHERE owner_id=p_owner AND (event_id=item->>'eventId' OR (device_id=p_device AND sequence=(item->>'sequence')::bigint)) LIMIT 1;
    IF FOUND THEN
      IF prior.event_id IS DISTINCT FROM item->>'eventId' OR prior.request_hash IS DISTINCT FROM encode(sha256(convert_to(item::text,'UTF8')),'hex') THEN
        RAISE SQLSTATE 'PT409' USING MESSAGE='IDEMPOTENCY_CONFLICT';
      END IF;
      duplicates := duplicates || jsonb_build_array(item->>'eventId');
      CONTINUE;
    END IF;
    IF pref.sync_records IS FALSE THEN
      RAISE SQLSTATE 'PT403' USING MESSAGE='RECORD_SYNC_DISABLED';
    END IF;
    IF NOT coalesce(pref.sync_paths, FALSE) AND ((item->'payload') ?| ARRAY['source','paths']) THEN
      RAISE SQLSTATE 'PT403' USING MESSAGE='PATH_SYNC_DISABLED';
    END IF;
    INSERT INTO public.sync_events(owner_id,device_id,event_id,sequence,body_json,request_hash,created_at)
      VALUES(p_owner,p_device,item->>'eventId',(item->>'sequence')::bigint,item::json,encode(sha256(convert_to(item::text,'UTF8')),'hex'),CURRENT_TIMESTAMP);
    accepted := accepted || jsonb_build_array(item->>'eventId');
  END LOOP;
  RETURN jsonb_build_object('acceptedEventIds',accepted,'duplicateEventIds',duplicates,'rejected','[]'::jsonb);
END;
$function$;
REVOKE ALL ON FUNCTION public.yingxu_ingest_batch(TEXT,TEXT,JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_ingest_batch(TEXT,TEXT,JSONB) TO service_role;

INSERT INTO schema_migrations(version,applied_at) VALUES(6,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
