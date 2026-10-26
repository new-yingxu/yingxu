"""Local HTTP preset revision protocol and fail-closed request validation."""
import unittest
import test_graph_checkpoint_http as fixtures

class PresetHTTP(unittest.TestCase):
    setUp=fixtures.GraphCheckpointHTTP.setUp
    tearDown=fixtures.GraphCheckpointHTTP.tearDown
    def put(self,preset,**changes):
        return self.client.put('/v1/presets/'+preset,json={'name':'日期','pattern':'{date}_{seq}','isDefault':False,'expectedVersion':0,**changes},headers=self.headers)
    def test_cas_defaults_feed_tombstone_and_idempotency(self):
        self.assertEqual(self.put('one',isDefault=True).status_code,200)
        self.assertTrue(self.put('one',isDefault=True).json()['duplicate'])
        self.assertEqual(self.put('one',name='Conflicting').status_code,409)
        self.assertEqual(self.put('two',isDefault=True).status_code,200)
        feed=self.client.get('/v1/presets/changes',headers=self.headers).json()
        self.assertEqual([(r['presetId'],r['version'],r['body']['isDefault']) for r in feed['changes']],[('one',1,True),('one',2,False),('two',1,True)])
        args={'headers':self.headers,'json':{'expectedVersion':1,'confirmed':True}}
        deleted=self.client.request('DELETE','/v1/presets/two',**args)
        self.assertEqual(deleted.status_code,200,deleted.text);self.assertTrue(deleted.json()['revision']['deleted'])
        self.assertTrue(self.client.request('DELETE','/v1/presets/two',**args).json()['duplicate'])
        delta=self.client.get('/v1/presets/changes?after_id='+str(feed['nextCursor']),headers=self.headers).json()
        self.assertEqual(len(delta['changes']),1);self.assertTrue(delta['changes'][0]['deleted'])
        self.assertEqual(self.put('two').status_code,409)
        self.assertEqual(self.put('two',expectedVersion=2).status_code,200)
    def test_injection_confirmation_preference_and_device_guard(self):
        self.assertEqual(self.put('one',ownerId='forged').status_code,422)
        self.assertEqual(self.put('one',expectedVersion=True).status_code,422)
        self.assertEqual(self.put('one',isDefault='true').status_code,422)
        self.assertEqual(self.put('one').status_code,200)
        for confirm in (False,1):
            response=self.client.request('DELETE','/v1/presets/one',json={'expectedVersion':1,'confirmed':confirm},headers=self.headers)
            self.assertEqual(response.status_code,422,response.text)
        preferences={'syncRecords':False,'syncAgentSessions':True,'syncThumbnails':False,'syncPaths':False}
        self.assertEqual(self.client.put('/v1/account/sync-preferences',json=preferences,headers=self.headers).status_code,200)
        self.assertEqual(self.put('blocked').status_code,403)
        self.assertEqual(self.client.get('/v1/presets/changes',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/v1/presets/changes?after_id=-1',headers=self.headers).status_code,422)
        from unittest.mock import patch
        from app.store import DeviceUnavailable
        with patch('app.main.PresetStore.put',side_effect=DeviceUnavailable()):
            self.assertEqual(self.put('race').status_code,403)
