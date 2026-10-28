"""Local HTTP leases: fixed event watermark and current privacy projection."""
import unittest
from sqlalchemy import select
from datetime import timedelta
import test_graph_checkpoint_http as fixtures
from app.main import app
from app.models import utcnow
from app.sync_snapshots import SyncSnapshotLease

class SnapshotHTTP(unittest.TestCase):
    setUp=fixtures.GraphCheckpointHTTP.setUp
    tearDown=fixtures.GraphCheckpointHTTP.tearDown
    def event(self,number,paths=False):
        payload={'jobId':'job-'+str(number),'name':'Fixture','state':'running','phase':'copy','files':1,'bytes':1,'progress':1,'targets':[]}
        if paths:payload['paths']=['PRIVATE_PATH']
        event={'schemaVersion':1,'eventId':'event-'+str(number),'deviceId':'local-dev-device','sequence':number,'eventType':'job.snapshot.v1','occurredAt':utcnow().isoformat(),'payload':payload}
        response=self.client.post('/v1/sync/events',json={'events':[event]},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
    def begin(self):return self.client.post('/v1/sync/snapshots',json={},headers=self.headers)
    def test_fixed_pages_exclude_new_event_then_incremental_handoff(self):
        self.event(1);self.event(2)
        start=self.begin();self.assertEqual(start.status_code,200,start.text)
        lease=start.json();self.assertEqual(lease['scope'],'synced_job_events')
        url='/v1/sync/snapshots/'+lease['snapshotId']
        first=self.client.get(url,params={'limit':1},headers=self.headers).json()
        self.assertTrue(first['hasMore']);self.event(3)
        last=self.client.get(url,params={'after_id':first['nextCursor'],'limit':1},headers=self.headers).json()
        self.assertFalse(last['hasMore']);self.assertEqual(last['events'][0]['eventId'],'event-2')
        self.assertEqual(last['throughCursor'],lease['throughCursor'])
        delta=self.client.get('/v1/sync/snapshot',params={'after_id':lease['throughCursor']},headers=self.headers).json()
        self.assertEqual([event['eventId'] for event in delta['events']],['event-3'])
    def test_redaction_expiry_and_parameter_guards(self):
        prefs={'syncRecords':True,'syncAgentSessions':True,'syncThumbnails':False,'syncPaths':True}
        self.assertEqual(self.client.put('/v1/account/sync-preferences',json=prefs,headers=self.headers).status_code,200)
        self.event(1,True);lease=self.begin().json();url='/v1/sync/snapshots/'+lease['snapshotId']
        self.assertIn('PRIVATE_PATH',self.client.get(url,headers=self.headers).text)
        prefs['syncPaths']=False
        self.assertEqual(self.client.put('/v1/account/sync-preferences',json=prefs,headers=self.headers).status_code,200)
        self.assertNotIn('PRIVATE_PATH',self.client.get(url,headers=self.headers).text)
        with app.state.session_factory.begin() as db:
            row=db.get(SyncSnapshotLease,lease['snapshotId']);row.expires_at=utcnow()-timedelta(seconds=1)
        self.assertEqual(self.client.get(url,headers=self.headers).status_code,410)
        self.assertEqual(self.client.get(url,params={'limit':501},headers=self.headers).status_code,422)
        self.assertEqual(self.client.post('/v1/sync/snapshots',json={'ownerId':'other'},headers=self.headers).status_code,422)
        self.assertEqual(self.client.get('/v1/sync/snapshots/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',headers=self.headers).status_code,404)
