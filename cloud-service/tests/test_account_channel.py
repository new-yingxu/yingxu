"""No-network account/client channel contract with mock HTTP transport."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock,patch
from urllib.error import HTTPError,URLError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.cloudbase_identity import verify_account_channel,InvalidCloudBaseIdentity,CloudBaseIdentityUnavailable,NoIdentityRedirect


class AccountChannel(unittest.TestCase):
    def setUp(self):
        self.profile={'sub':'owner','status':'ACTIVE','type':'external','internal_user_type':'generalUser','providers':[{'id':'email'}],
                      'origin':'mobile','device_id':'not-trusted','email':'private-profile'}
        self.introspection={'sub':'owner','token_type':'Bearer','client_id':'server-desktop','active':True}
        self.clients={'server-desktop':'desktop','server-mobile':'mobile'}
    def call(self,info=None,profile=None):
        opener=MagicMock()
        queue=[json.dumps(self.profile if profile is None else profile).encode(),json.dumps(self.introspection if info is None else info).encode()]
        def open_request(request,timeout):
            self.assertEqual(timeout,5)
            transport=MagicMock();transport.__enter__.return_value.read.return_value=queue.pop(0)
            return transport
        opener.open.side_effect=open_request
        with patch('app.cloudbase_identity.build_opener',return_value=opener) as factory:
            value=verify_account_channel('env-fixture','synthetic-provider-token',self.clients)
        return value,opener,factory

    def test_both_fixed_same_environment_apis_and_server_client_map(self):
        value,opener,factory=self.call()
        self.assertEqual((value.owner_id,value.origin,value.client_id),('owner','desktop','server-desktop'))
        self.assertEqual(set(value.__dict__),{'owner_id','origin','client_id'})
        self.assertNotIn('synthetic-provider-token',repr(value));self.assertNotIn('private-profile',repr(value))
        calls=opener.open.call_args_list
        self.assertEqual([c.args[0].full_url for c in calls],['https://env-fixture.api.tcloudbasegateway.com/auth/v1/user/me',
                                                                          'https://env-fixture.api.tcloudbasegateway.com/auth/v1/token/introspect'])
        for call in calls:
            self.assertEqual(call.args[0].get_method(),'GET')
            self.assertEqual(set(call.args[0].headers),{'Authorization','Accept'})
        self.assertTrue(all(isinstance(c.args[0],NoIdentityRedirect) for c in factory.call_args_list))
        mobile,_,_=self.call({**self.introspection,'client_id':'server-mobile'})
        self.assertEqual(mobile.origin,'mobile')

    def test_mismatch_unlisted_bad_token_type_and_inactive_rejected(self):
        for changes in ({'sub':'victim'},{'sub':None},{'client_id':'unlisted'},{'client_id':[]},{'token_type':'bearer'},
                        {'token_type':None},{'active':False},{'active':'true'}):
            with self.subTest(changes=changes),self.assertRaises(InvalidCloudBaseIdentity):
                self.call({**self.introspection,**changes})
        with self.assertRaises(InvalidCloudBaseIdentity):self.call(profile={**self.profile,'status':'BLOCKED'})

    def test_malformed_oversize_timeout_and_auth_transport_fail_closed(self):
        for value,expected in [(b'[]',CloudBaseIdentityUnavailable),(b'{broken',CloudBaseIdentityUnavailable),
                               (b'x'*65537,CloudBaseIdentityUnavailable),(TimeoutError('private'),CloudBaseIdentityUnavailable),
                               (URLError('private'),CloudBaseIdentityUnavailable),
                               (HTTPError('https://ignored.invalid',401,'private',{},None),InvalidCloudBaseIdentity),
                               (HTTPError('https://ignored.invalid',503,'private',{},None),CloudBaseIdentityUnavailable)]:
            with self.subTest(value=type(value).__name__):
                opener=MagicMock()
                first=MagicMock();first.__enter__.return_value.read.return_value=json.dumps(self.profile).encode()
                second=MagicMock();second.__enter__.return_value.read.return_value=value if isinstance(value,bytes) else b''
                opener.open.side_effect=[first,value if isinstance(value,Exception) else second]
                with patch('app.cloudbase_identity.build_opener',return_value=opener),self.assertRaises(expected) as error:
                    verify_account_channel('env-fixture','synthetic-provider-token',self.clients)
                self.assertNotIn('private',str(error.exception))
                self.assertEqual(opener.open.call_count,2)

    def test_bad_config_and_header_injection_make_no_network_requests(self):
        for env,token,clients in [('env/evil','token',self.clients),('env','token\r\nInjected: x',self.clients),
                                  ('env','token',{}),('env','token',{'x':'unknown'}),('env','token',{'bad\nclient':'desktop'})]:
            with self.subTest(env=env),patch('app.cloudbase_identity.build_opener') as network,self.assertRaises(ValueError):
                verify_account_channel(env,token,clients)
            network.assert_not_called()


if __name__=='__main__':unittest.main()
