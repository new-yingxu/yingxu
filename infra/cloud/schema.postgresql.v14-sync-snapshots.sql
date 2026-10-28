-- Local preparation only. Stable-watermark job-event snapshots; requires v13.
BEGIN;
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE version=13) THEN RAISE EXCEPTION 'v13 required'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.sync_snapshot_leases (
 snapshot_id VARCHAR(36) PRIMARY KEY,
 owner_id VARCHAR(160) NOT NULL,
 device_id VARCHAR(500) NOT NULL,
 origin VARCHAR(16) NOT NULL CHECK(origin IN ('desktop','mobile')),
 through_cursor BIGINT NOT NULL CHECK(through_cursor>=0),
 expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sync_snapshot_leases_owner_device ON public.sync_snapshot_leases(owner_id,device_id,origin);
CREATE INDEX IF NOT EXISTS sync_snapshot_leases_expires ON public.sync_snapshot_leases(expires_at);
ALTER TABLE public.sync_snapshot_leases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sync_snapshot_leases FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,DELETE ON public.sync_snapshot_leases TO service_role;
CREATE OR REPLACE FUNCTION public.yingxu_sync_snapshot(
 p_owner TEXT,p_device TEXT,p_origin TEXT,p_operation TEXT,
 p_snapshot TEXT DEFAULT NULL,p_after BIGINT DEFAULT 0,p_limit INTEGER DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public
AS $fn$
DECLARE lease public.sync_snapshot_leases%ROWTYPE; result JSONB; v_now TIMESTAMPTZ; total INTEGER;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 160 OR length(btrim(p_owner))=0 OR p_owner ~ '[[:cntrl:]]'
 OR p_device IS NULL OR length(p_device) NOT BETWEEN 1 AND 500 OR length(btrim(p_device))=0 OR p_device ~ '[[:cntrl:]]'
 OR p_origin IS NULL OR p_origin NOT IN ('desktop','mobile')
 OR p_operation IS NULL OR p_operation NOT IN ('begin','page')
 OR p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500
 OR (p_operation='page' AND (p_snapshot IS NULL OR p_snapshot !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')) THEN
 RAISE SQLSTATE 'PT422' USING MESSAGE='INVALID_SYNC_SNAPSHOT'; END IF;
 INSERT INTO public.sync_owner_locks(owner_id) VALUES(p_owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=EXCLUDED.owner_id;
 PERFORM id FROM public.devices WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin AND NOT revoked FOR SHARE;
 IF NOT FOUND THEN RAISE SQLSTATE 'PT403' USING MESSAGE='DEVICE_REVOKED_OR_UNREGISTERED'; END IF;
 v_now:=clock_timestamp();
 IF p_operation='begin' THEN
 DELETE FROM public.sync_snapshot_leases WHERE owner_id=p_owner AND expires_at<=v_now;
 SELECT count(*) INTO total FROM public.sync_snapshot_leases WHERE owner_id=p_owner AND device_id=p_device AND origin=p_origin;
 IF total>=20 THEN RETURN jsonb_build_object('error','SNAPSHOT_LIMIT'); END IF;
 lease.snapshot_id:=gen_random_uuid()::text;lease.owner_id:=p_owner;lease.device_id:=p_device;lease.origin:=p_origin;
 SELECT COALESCE(max(id),0) INTO lease.through_cursor FROM public.sync_events WHERE owner_id=p_owner;
 lease.expires_at:=v_now+interval '300 seconds';
 INSERT INTO public.sync_snapshot_leases(snapshot_id,owner_id,device_id,origin,through_cursor,expires_at)
 VALUES(lease.snapshot_id,lease.owner_id,lease.device_id,lease.origin,lease.through_cursor,lease.expires_at);
 RETURN jsonb_build_object('snapshotId',lease.snapshot_id,'throughCursor',lease.through_cursor,'expiresAt',lease.expires_at,'scope','synced_job_events');
 END IF;
 SELECT * INTO lease FROM public.sync_snapshot_leases WHERE snapshot_id=p_snapshot AND owner_id=p_owner AND device_id=p_device AND origin=p_origin;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','SNAPSHOT_NOT_FOUND'); END IF;
 IF lease.expires_at<=v_now THEN RETURN jsonb_build_object('error','SNAPSHOT_EXPIRED'); END IF;
 IF p_after>lease.through_cursor THEN RAISE SQLSTATE 'PT422' USING MESSAGE='SNAPSHOT_CURSOR_EXCEEDS_BOUNDARY'; END IF;
 SELECT jsonb_build_object('events',COALESCE(jsonb_agg(body_json ORDER BY id) FILTER(WHERE seq<=p_limit),'[]'::jsonb),
 'nextCursor',COALESCE(max(id) FILTER(WHERE seq<=p_limit),p_after),'hasMore',count(*)>p_limit)
 INTO result FROM (SELECT id,body_json,row_number() OVER(ORDER BY id) seq FROM public.sync_events WHERE owner_id=p_owner AND id>p_after AND id<=lease.through_cursor ORDER BY id LIMIT p_limit+1) page;
 RETURN result||jsonb_build_object('snapshotId',lease.snapshot_id,'throughCursor',lease.through_cursor,'expiresAt',lease.expires_at,'scope','synced_job_events');
END;
$fn$;
REVOKE ALL ON FUNCTION public.yingxu_sync_snapshot(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,INTEGER) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yingxu_sync_snapshot(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,INTEGER) TO service_role;
INSERT INTO public.schema_migrations(version,applied_at) VALUES(14,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
COMMIT;
