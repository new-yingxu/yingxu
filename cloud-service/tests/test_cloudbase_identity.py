import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock,patch
from urllib.error import HTTPError,URLError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.cloudbase_identity import verify_access_token,InvalidCloudBaseIdentity,CloudBaseIdentityUnavailable


class CloudBaseIdentity(unittest.TestCase):
    def profile(self):
        return {'sub':'owner-fixture','status':'ACTIVE','type':'external','internal_user_type':'generalUser','providers':[{'id':'email'}],'email':'private@example.invalid','phone_number':'private','device_id':'untrusted','origin':'desktop'}

    def call(self,profile):
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value=json.dumps(profile).encode()
        with patch('app.cloudbase_identity.build_opener',return_value=opener):
            account=verify_access_token('env-fixture','synthetic-user-token','configured-client')
        return account,opener

    def test_verified_account_does_not_grant_device_or_roles(self):
        account,opener=self.call(self.profile())
        self.assertEqual(account.owner_id,'owner-fixture')
        for field in ('device_id','origin','email','phone_number','roles','access_token'):
            self.assertFalse(hasattr(account,field))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://env-fixture.api.tcloudbasegateway.com/auth/v1/user/me?client_id=configured-client')
        self.assertNotIn('X-device-id',request.headers)
        self.assertEqual(opener.open.call_args.kwargs['timeout'],5)
        self.assertNotIn('synthetic-user-token',repr(account))

    def test_inactive_admin_and_anonymous_profiles_fail_closed(self):
        for changes in ({'status':'BLOCKED'},{'status':'PENDING'},{'status':'DEFAULT'},{'type':'internal'},{'internal_user_type':'administrator'},{'sub':''},{'providers':[{'id':'anonymous'}]}):
            with self.subTest(changes=changes),self.assertRaises(InvalidCloudBaseIdentity):
                self.call({**self.profile(),**changes})

    def test_provider_error_is_generic_and_no_invalid_token_request(self):
        opener=MagicMock()
        opener.open.side_effect=HTTPError('https://provider.invalid',401,'private provider data',{},None)
        with patch('app.cloudbase_identity.build_opener',return_value=opener),self.assertRaises(InvalidCloudBaseIdentity) as error:
            verify_access_token('env-fixture','synthetic-user-token')
        self.assertNotIn('private provider data',str(error.exception))
        with patch('app.cloudbase_identity.build_opener') as network,self.assertRaises(InvalidCloudBaseIdentity):
            verify_access_token('env-fixture','invalid\r\nheader')
        network.assert_not_called()
        opener.open.side_effect=URLError('private internal details')
        with patch('app.cloudbase_identity.build_opener',return_value=opener),self.assertRaises(CloudBaseIdentityUnavailable) as error:
            verify_access_token('env-fixture','synthetic-user-token')
        self.assertNotIn('private internal',str(error.exception))
