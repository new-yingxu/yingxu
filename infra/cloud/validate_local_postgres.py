"""Apply all migrations and run contracts on a disposable, socket-only PG cluster.

Requires local PostgreSQL binaries and the cloud-service Python dependencies.
Never connects to the cloud. Logs/cluster are retained for diagnosis and replay.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import psycopg
from sqlalchemy.engine import URL


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bin',default='/opt/homebrew/opt/postgresql@17/bin')
    args=parser.parse_args()
    pg=Path(args.bin)
    for command in ('initdb','pg_ctl'):
        if not (pg/command).is_file():
            parser.error('PostgreSQL binaries missing: '+str(pg/command))
    root=Path(__file__).resolve().parents[2]
    evidence=Path(tempfile.mkdtemp(prefix='yingxu-pg-',dir='/tmp'))
    socket=evidence/'socket'
    socket.mkdir(mode=0o700)
    data=evidence/'data'
    log=evidence/'validation.log'
    started=False
    print('Evidence retained:',evidence,flush=True)
    try:
        with log.open('w') as output:
            subprocess.run([str(pg/'initdb'),'-D',str(data),'-A','trust','--no-locale','-E','UTF8'],stdout=output,stderr=subprocess.STDOUT,check=True)
            subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-l',str(evidence/'postgres.log'),'-o',"-h '' -k "+str(socket),'-w','start'],stdout=output,stderr=subprocess.STDOUT,check=True)
            started=True
            with psycopg.connect(host=str(socket),dbname='postgres',autocommit=True) as db:
                db.execute('CREATE ROLE anon NOLOGIN')
                db.execute('CREATE ROLE authenticated NOLOGIN')
                db.execute('CREATE ROLE service_role NOLOGIN BYPASSRLS')
                migrations=[root/'infra/cloud/schema.postgresql.sql']+sorted((root/'infra/cloud').glob('schema.postgresql.v*.sql'),key=lambda p:int(p.name.split('.v')[1].split('-')[0]))
                for migration in migrations:
                    db.execute(migration.read_text())
                    print('Applied',migration.name,flush=True)
                db.execute((root/'infra/cloud/schema.postgresql.v11-graph-checkpoints.sql').read_text())
                print('Reapplied v11 idempotently',flush=True)
                db.execute((root/'infra/cloud/schema.postgresql.v12-presets.sql').read_text())
                print('Reapplied v12 idempotently',flush=True)
                # Reapply the whole tail in ascending order. Reapplying an older
                # function migration after v15 would intentionally replace the
                # newer local-step cleanup functions and make the test DB differ
                # from a correctly upgraded database.
                for tail_version in (13,14,15,16):
                    migration=next((root/'infra/cloud').glob('schema.postgresql.v'+str(tail_version)+'-*.sql'))
                    db.execute(migration.read_text())
                    print('Reapplied v'+str(tail_version)+' idempotently',flush=True)
                print('PostgreSQL version:',db.execute('SHOW server_version').fetchone()[0],flush=True)
                tables=('data_cleanup_receipts','sync_snapshot_leases','local_agent_steps','preset_revisions','agent_graph_checkpoints','agent_graph_pending_writes','agent_model_requests','devices','sync_events','agent_actions','sync_preferences','schema_migrations','sync_owner_locks','agent_turns','deleted_agent_sessions','sync_invalidations')
                for role in ('anon','authenticated'):
                    for table in tables:
                        allowed=db.execute('SELECT has_table_privilege(%s,%s,%s)',(role,'public.'+table,'SELECT,INSERT,UPDATE,DELETE')).fetchone()[0]
                        if allowed:raise AssertionError(role+' retains table access: '+table)
                for function in ('yingxu_clear_sync_data(text,text,text,text,boolean)','yingxu_sync_snapshot(text,text,text,text,text,bigint,integer)','yingxu_presets(text,text,text,text,text,jsonb,integer,boolean,bigint,integer)','yingxu_graph_checkpoint(text,text,text,text,text,text,text,text,jsonb)','yingxu_reserve_model(text,text,text,text,text,text)','yingxu_finish_model(text,text,text,text,text,text,jsonb)','yingxu_ingest_batch(text,text,jsonb)','yingxu_set_preferences(text,boolean,boolean,boolean,boolean)','yingxu_save_turn(text,text,text,text,text,jsonb)','yingxu_delete_session(text,text,text)','yingxu_local_agent_step(text,text,text,text,text,text,integer,jsonb)'):
                    for role in ('anon','authenticated'):
                        if db.execute('SELECT has_function_privilege(%s,%s,%s)',(role,'public.'+function,'EXECUTE')).fetchone()[0]:
                            raise AssertionError(role+' retains RPC access: '+function)
                print('Client table/RPC privilege checks passed',flush=True)
            env=dict(os.environ,YINGXU_TEST_PG_URL=URL.create('postgresql+psycopg',database='postgres',query={'host':str(socket)}).render_as_string(hide_password=False))
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s','cloud-service/tests','-v'],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            output.write(result.stdout)
            print(result.stdout,flush=True)
            return result.returncode
    finally:
        if started:
            subprocess.run([str(pg/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,check=False)


if __name__=='__main__':
    raise SystemExit(main())
