"""Offline named-preset CAS, append-only feed and principal boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Base
from app.models import Device,SyncPreferences
from app.store import SQLStore,DeviceUnavailable
from app.preset_store import PresetStore,PresetRevision,PresetConflict,PresetNotFound,PresetLimit,PresetSyncDisabled


class Presets(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/presets.sqlite')
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        with self.sessions.begin() as db:
            db.add_all([Device(owner_id='owner',device_id='desktop',origin='desktop'),Device(owner_id='other',device_id='desktop',origin='desktop')])
        self.db=self.sessions();self.store=SQLStore(self.db)
        self.principal=SimpleNamespace(owner_id='owner',device_id='desktop',origin='desktop')
        self.presets=PresetStore(self.store,self.principal)
        self.body={'name':'日期序号','pattern':'{date}_{seq}','isDefault':False}
    def tearDown(self):self.db.close();self.engine.dispose();self.temp.cleanup()
    def put(self,preset='one',expected=0,**changes):return self.presets.put(preset,{**self.body,**changes},expected)

    def test_create_update_exact_replay_stale_conflict_and_append_only(self):
        first=self.put();self.assertFalse(first['duplicate']);self.assertEqual(first['revision']['version'],1)
        replay=self.put();self.assertTrue(replay['duplicate']);self.assertEqual(replay['revision'],first['revision'])
        with self.assertRaises(PresetConflict):self.put(name='different')
        second=self.put(expected=1,name='新名称');self.assertEqual(second['revision']['version'],2)
        self.assertTrue(self.put(expected=1,name='新名称')['duplicate'])
        with self.assertRaises(PresetConflict):self.put(expected=0)
        revisions=self.db.scalars(select(PresetRevision).order_by(PresetRevision.id)).all()
        self.assertEqual(len(revisions),2);self.assertEqual(revisions[0].body_json,self.body)
        self.assertGreater(second['revision']['id'],first['revision']['id'])

    def test_default_switch_demotes_other_with_visible_new_revision(self):
        a=self.put('a',isDefault=True)['revision']
        b=self.put('b',isDefault=True)['revision']
        feed=self.presets.changes(0,100)['changes']
        self.assertEqual([(r['presetId'],r['version'],r['body']['isDefault']) for r in feed],[('a',1,True),('a',2,False),('b',1,True)])
        self.assertLess(feed[1]['id'],b['id']);self.assertFalse(feed[1]['deleted'])
        self.assertTrue(self.put('b',isDefault=True)['duplicate'])
        self.assertEqual(len(self.presets.changes()['changes']),3)
        with self.assertRaises(PresetConflict):self.put('a',expected=1,isDefault=True)
        self.put('a',expected=2,isDefault=True)
        latest={r['presetId']:r for r in self.presets.changes()['changes']}
        self.assertEqual(sum(r['body']['isDefault'] for r in latest.values() if not r['deleted']),1)

    def test_delete_tombstone_confirmation_idempotency_and_recreation(self):
        self.put()
        for confirmed in (False,None,1,'true'):
            with self.assertRaises(ValueError):self.presets.delete('one',1,confirmed)
        with self.assertRaises(PresetNotFound):self.presets.delete('missing',0,True)
        with self.assertRaises(PresetConflict):self.presets.delete('one',0,True)
        deleted=self.presets.delete('one',1,True)
        self.assertTrue(deleted['revision']['deleted']);self.assertEqual(deleted['revision']['version'],2)
        self.assertTrue(self.presets.delete('one',1,True)['duplicate'])
        removed=self.presets.changes()['changes']
        self.assertTrue(all(row['body']=={} and row['deleted'] for row in removed))
        self.assertNotIn('日期序号',str(self.db.scalars(select(PresetRevision.body_json)).all()))
        with self.assertRaises(PresetConflict):self.put()
        recreated=self.put(expected=2)
        self.assertFalse(recreated['revision']['deleted']);self.assertEqual(recreated['revision']['version'],3)
        rows=self.presets.changes()['changes'];self.assertEqual(len(rows),3);self.assertTrue(rows[1]['deleted'])

    def test_changes_pagination_and_owner_isolation(self):
        for i in range(5):self.put(str(i))
        other=PresetStore(self.store,SimpleNamespace(owner_id='other',device_id='desktop',origin='desktop'))
        self.assertEqual(other.changes()['changes'],[])
        other.put('0',self.body,0)
        with self.assertRaises(PresetNotFound):other.delete('1',0,True)
        cursor=0;ids=[]
        while True:
            page=self.presets.changes(cursor,2)
            ids.extend(row['id'] for row in page['changes']);cursor=page['nextCursor']
            if not page['hasMore']:break
        self.assertEqual(len(ids),5);self.assertEqual(ids,sorted(set(ids)))
        self.assertEqual(self.presets.changes(cursor,2),{'changes':[],'nextCursor':cursor,'hasMore':False})
        for after,limit in [(-1,2),(True,1),(0,0),(0,101),(0,True)]:
            with self.assertRaises(ValueError):self.presets.changes(after,limit)

    def test_sync_preference_blocks_writes_but_existing_reads_remain(self):
        self.put()
        with self.sessions.begin() as db:db.add(SyncPreferences(owner_id='owner',sync_records=False))
        with self.assertRaises(PresetSyncDisabled):self.put('blocked')
        with self.assertRaises(PresetSyncDisabled):self.presets.delete('one',1,True)
        self.assertEqual(len(self.presets.changes()['changes']),1)
        with self.sessions.begin() as db:db.get(SyncPreferences,'owner').sync_records=True
        self.put('new')

    def test_revoked_unregistered_and_origin_mismatch_deny_read_and_write(self):
        self.put()
        for device,origin in [('missing','desktop'),('desktop','mobile')]:
            scoped=PresetStore(self.store,SimpleNamespace(owner_id='owner',device_id=device,origin=origin))
            with self.assertRaises(DeviceUnavailable):scoped.changes()
            with self.assertRaises(DeviceUnavailable):scoped.put('blocked',self.body,0)
        with self.sessions.begin() as db:db.scalar(select(Device).where(Device.owner_id=='owner')).revoked=True
        for call in (lambda:self.presets.changes(),lambda:self.put('blocked'),lambda:self.presets.delete('one',1,True)):
            with self.assertRaises(DeviceUnavailable):call()

    def test_active_limit_one_hundred_delete_frees_capacity(self):
        for i in range(100):self.put(str(i))
        self.assertTrue(self.put('0')['duplicate'])
        self.put('0',expected=1,name='updated')
        with self.assertRaises(PresetLimit):self.put('overflow',isDefault=True)
        self.presets.delete('1',1,True)
        self.put('overflow',isDefault=True)
        with self.assertRaises(PresetLimit):self.put('1',expected=2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(PresetRevision)),103)

    def test_rule_validation_unknown_fields_and_versions_are_strict(self):
        for changes in ({'name':''},{'name':' '*3},{'name':'x'*121},{'pattern':''},{'pattern':'x'*501},
                        {'isDefault':'true'},{'isDefault':1},{'owner_id':'victim'},{'paths':['private']},{'fileContents':'private'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.presets.put('one',{**self.body,**changes},0)
        for preset,version in [('',0),('x'*161,0),('one',True),('one',-1),('one',2**31-1)]:
            with self.assertRaises(ValueError):self.put(preset,version)
        self.assertEqual(self.presets.changes()['changes'],[])

    def test_rpc_is_bound_to_server_principal_and_maps_semantic_errors(self):
        store=MagicMock();store.request.return_value={'changes':[],'nextCursor':0,'hasMore':False}
        rpc=PresetStore(store,self.principal)
        rpc.changes(0,10)
        args=store.request.call_args
        self.assertEqual(args.args,('rpc/yingxu_presets','POST'))
        self.assertEqual(args.kwargs['body'],{'p_owner':'owner','p_device':'desktop','p_origin':'desktop','p_operation':'changes','p_after':0,'p_limit':10})
        for error,expected in [('PRESET_CONFLICT',PresetConflict),('PRESET_NOT_FOUND',PresetNotFound),('PRESET_LIMIT',PresetLimit),('PRESET_SYNC_DISABLED',PresetSyncDisabled)]:
            store.request.return_value={'error':error}
            with self.assertRaises(expected):rpc.put('one',self.body,0)
        store.request.reset_mock()
        with self.assertRaises(ValueError):rpc.put('one',{**self.body,'owner':'victim'},0)
        store.request.assert_not_called()


if __name__=='__main__':unittest.main()
