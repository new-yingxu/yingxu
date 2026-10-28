"""Real local HTTP, RSA sessions and database; synthetic CloudBase HTTP only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.session_tokens import verify_device_session

class SessionExchangeHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'APP_ENV':'development','AUTH_MODE':'cloudbase','DATABASE_URL':'sqlite:///'+self.temp.name+'/session.db',
            'CLOUDBASE_ENV_ID':'env-fixture','CLOUDBASE_CLIENT_ORIGINS':json.dumps({'desktop-client':'desktop','mobile-client':'mobile'}),
            'SESSION_PRIVATE_KEY':self.private,'SESSION_KEY_ID':'fixture-key','JWT_ISSUER':'https://issuer.invalid','JWT_AUDIENCE':'yingxu-session'})
        self.env.start();self.client=TestClient(app,raise_server_exceptions=False);self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.temp.cleanup()
    def exchange(self,owner='owner',client='desktop-client',**changes):
        profiles=[{'sub':owner,'token_type':'Bearer','client_id':client,'scope':'user'},
                  {'sub':owner,'status':'ACTIVE','type':'external','internal_user_type':'generalUser','providers':[{'id':'email'}]}]
        opener=MagicMock()
        responses=[]
        for profile in profiles:
            response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(profile).encode();responses.append(response)
        opener.open.side_effect=responses
        with patch('app.cloudbase_identity.build_opener',return_value=opener):
            return self.client.post('/v1/auth/session/exchange',json={'displayName':'Fixture',**changes},headers={'Authorization':'Bearer synthetic-cloud-account','x-device-id':'forged-device','x-origin':'mobile'})
    def test_real_signed_exchange_owner_isolation_server_device_and_logout(self):
        first=self.exchange();self.assertEqual(first.status_code,200,first.text)
        data=first.json();self.assertEqual(first.headers['cache-control'],'no-store')
        self.assertEqual(data['origin'],'desktop');self.assertNotEqual(data['deviceId'],'forged-device')
        self.assertFalse(data['deviceAttested']);self.assertEqual(data['expiresIn'],900)
        self.assertNotIn('synthetic-cloud-account',first.text);self.assertNotIn('PRIVATE KEY',first.text)
        verified=verify_device_session(data['accessToken'],self.private,'fixture-key','https://issuer.invalid','yingxu-session')
        self.assertEqual(verified.device_id,data['deviceId'])
        headers={'Authorization':'Bearer '+data['accessToken']}
        other=self.exchange(owner='other',client='mobile-client').json()
        other_headers={'Authorization':'Bearer '+other['accessToken']}
        self.assertEqual([d['deviceId'] for d in self.client.get('/v1/devices',headers=headers).json()],[data['deviceId']])
        self.assertEqual([d['deviceId'] for d in self.client.get('/v1/devices',headers=other_headers).json()],[other['deviceId']])
        jwks=self.client.get('/.well-known/jwks.json').json();self.assertNotIn('d',jwks['keys'][0])
        self.assertEqual(self.client.post('/v1/auth/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.post('/v1/auth/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/v1/devices',headers=headers).status_code,403)
        self.assertEqual(self.client.post('/v1/devices/register',json={'displayName':'Resurrection'},headers=headers).status_code,403)
        self.assertEqual(self.client.get('/v1/devices',headers=other_headers).status_code,200)
        replacement=self.exchange().json();self.assertNotEqual(replacement['deviceId'],data['deviceId'])
    def test_unlisted_client_injection_and_missing_auth_fail_closed(self):
        self.assertEqual(self.exchange(client='unlisted').status_code,401)
        self.assertEqual(self.exchange(ownerId='forged').status_code,422)
        self.assertEqual(self.client.post('/v1/auth/session/exchange',json={'displayName':'Fixture'}).status_code,401)
        self.assertEqual(self.client.get('/v1/devices',headers={'Authorization':'Bearer synthetic-cloud-account'}).status_code,401)
        self.assertNotIn(self.private,repr(app.state.settings))
    def test_missing_signing_config_never_calls_identity_provider(self):
        from dataclasses import replace
        app.state.settings=replace(app.state.settings,session_private_key=None)
        with patch('app.cloudbase_identity.verify_account_channel') as provider:
            response=self.client.post('/v1/auth/session/exchange',json={'displayName':'Fixture'},headers={'Authorization':'Bearer synthetic-cloud-account'})
        self.assertEqual(response.status_code,503);provider.assert_not_called()
        self.assertEqual(response.headers['cache-control'],'no-store')

    def test_live_key_rotation_keeps_old_sessions_until_key_removed(self):
        from dataclasses import replace
        from app.session_tokens import public_jwks
        first=self.exchange().json()
        old_headers={'Authorization':'Bearer '+first['accessToken']}
        previous=public_jwks(self.private,'fixture-key')
        next_private=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()
        app.state.settings=replace(app.state.settings,session_private_key=next_private,session_key_id='next-key',session_verification_jwks=json.dumps(previous))
        self.assertEqual(self.client.get('/v1/devices',headers=old_headers).status_code,200)
        second=self.exchange().json()
        new_headers={'Authorization':'Bearer '+second['accessToken']}
        self.assertEqual(self.client.get('/v1/devices',headers=new_headers).status_code,200)
        self.assertEqual({key['kid'] for key in self.client.get('/.well-known/jwks.json').json()['keys']},{'fixture-key','next-key'})
        app.state.settings=replace(app.state.settings,session_verification_jwks=None)
        self.assertEqual(self.client.get('/v1/devices',headers=old_headers).status_code,401)
        self.assertEqual(self.client.get('/v1/devices',headers=new_headers).status_code,200)
        self.assertEqual(len(self.client.get('/.well-known/jwks.json').json()['keys']),1)

    def renew(self,session,owner='owner',client='desktop-client',before_introspection=None,**body):
        profiles=[{'sub':owner,'token_type':'Bearer','client_id':client},
                  {'sub':owner,'status':'ACTIVE','type':'external','internal_user_type':'generalUser','providers':[{'id':'email'}]}]
        opener=MagicMock();index=0
        def open_request(request,timeout):
            nonlocal index
            self.assertEqual(app.state.engine.pool.checkedout(),0,'renew held a SQL connection during provider I/O')
            if index==0 and before_introspection is not None:before_introspection()
            response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(profiles[index]).encode()
            index+=1
            return response
        opener.open.side_effect=open_request
        with patch('app.cloudbase_identity.build_opener',return_value=opener):
            result=self.client.post('/v1/auth/session/renew',json=body,headers={'Authorization':'Bearer synthetic-cloud-account','X-Yingxu-Session':session})
        return result

    def test_renew_reauthenticates_same_device_fresh_jti_then_logout_revokes_both(self):
        import jwt
        from sqlalchemy import select,func
        from app.models import Device
        initial=self.exchange().json();old=initial['accessToken']
        renewed=self.renew(old)
        self.assertEqual(renewed.status_code,200,renewed.text)
        data=renewed.json();new=data['accessToken']
        self.assertEqual(data['deviceId'],initial['deviceId']);self.assertEqual(data['ownerId'],'owner')
        self.assertEqual(data['origin'],'desktop');self.assertEqual(data['expiresIn'],900)
        self.assertEqual(renewed.headers['cache-control'],'no-store')
        old_claims=jwt.decode(old,options={'verify_signature':False})
        new_claims=jwt.decode(new,options={'verify_signature':False})
        self.assertNotEqual(old_claims['jti'],new_claims['jti']);self.assertEqual(new_claims['exp']-new_claims['iat'],900)
        with app.state.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Device)),1)
        for token in (old,new):
            self.assertEqual(self.client.get('/v1/devices',headers={'Authorization':'Bearer '+token}).status_code,200)
        self.assertEqual(self.client.post('/v1/auth/logout',headers={'Authorization':'Bearer '+new}).status_code,200)
        for token in (old,new):
            self.assertEqual(self.client.get('/v1/devices',headers={'Authorization':'Bearer '+token}).status_code,403)
            with patch('app.cloudbase_identity.verify_account_channel') as provider:
                rejected=self.renew(token)
            self.assertEqual(rejected.status_code,403);provider.assert_not_called()
        raw=(Path(self.temp.name)/'session.db').read_bytes()
        for secret in (old,new,'synthetic-cloud-account',self.private):self.assertNotIn(secret.encode(),raw)

    def test_renew_wrong_account_origin_injection_and_missing_credentials(self):
        token=self.exchange().json()['accessToken']
        self.assertEqual(self.renew(token,owner='other').status_code,403)
        self.assertEqual(self.renew(token,client='mobile-client').status_code,403)
        for body in ({'ownerId':'other'},{'deviceId':'forged'},{'origin':'mobile'}):
            self.assertEqual(self.renew(token,**body).status_code,422)
        for headers in ({},{'Authorization':'Bearer synthetic-cloud-account'},{'X-Yingxu-Session':token}):
            with patch('app.cloudbase_identity.verify_account_channel') as provider:
                result=self.client.post('/v1/auth/session/renew',json={},headers=headers)
            self.assertEqual(result.status_code,401);provider.assert_not_called()
        self.assertEqual(self.renew(token,client='unlisted').status_code,401)

    def test_renew_rejects_invalid_expired_and_unregistered_sessions_before_provider(self):
        import time
        import jwt
        from app.session_tokens import issue_device_session
        token=self.exchange().json()['accessToken']
        claims=jwt.decode(token,options={'verify_signature':False});now=int(time.time())
        expired=jwt.encode({**claims,'iat':now-1000,'nbf':now-1000,'exp':now-100},self.private,algorithm='RS256',headers={'kid':'fixture-key'})
        unknown=issue_device_session(self.private,'fixture-key','https://issuer.invalid','yingxu-session',owner_id='owner',device_id='unknown',origin='desktop')
        for session,status in [('invalid-session',401),(expired,401),(unknown,403)]:
            with patch('app.cloudbase_identity.verify_account_channel') as provider:
                rejected=self.renew(session)
            self.assertEqual(rejected.status_code,status,rejected.text);provider.assert_not_called()

    def test_renew_provider_failure_and_concurrent_revocation_fail_closed(self):
        from app.cloudbase_identity import CloudBaseIdentityUnavailable
        from sqlalchemy import select
        from app.models import Device
        token=self.exchange().json()['accessToken']
        with patch('app.cloudbase_identity.verify_account_channel',side_effect=CloudBaseIdentityUnavailable('fixture')):
            unavailable=self.renew(token)
        self.assertEqual(unavailable.status_code,503,unavailable.text)
        def revoke():
            with app.state.session_factory.begin() as db:
                db.scalar(select(Device).where(Device.owner_id=='owner')).revoked=True
        result=self.renew(token,before_introspection=revoke)
        self.assertEqual(result.status_code,403,result.text)

    def test_renew_supports_old_public_key_and_signs_current_active_key(self):
        import jwt
        from dataclasses import replace
        from app.session_tokens import public_jwks
        old=self.exchange().json()['accessToken']
        previous=public_jwks(self.private,'fixture-key')
        active=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()
        app.state.settings=replace(app.state.settings,session_private_key=active,session_key_id='next-key',session_verification_jwks=json.dumps(previous))
        renewed=self.renew(old)
        self.assertEqual(renewed.status_code,200,renewed.text)
        self.assertEqual(jwt.get_unverified_header(renewed.json()['accessToken'])['kid'],'next-key')
        app.state.settings=replace(app.state.settings,session_verification_jwks=None)
        with patch('app.cloudbase_identity.verify_account_channel') as provider:
            rejected=self.renew(old)
        self.assertEqual(rejected.status_code,401);provider.assert_not_called()
        self.assertEqual(self.renew(renewed.json()['accessToken']).status_code,200)

    def test_renew_not_configured_fails_before_account_provider(self):
        from dataclasses import replace
        token=self.exchange().json()['accessToken']
        original=app.state.settings
        for settings in (replace(original,auth_mode='disabled'),replace(original,session_private_key=None)):
            app.state.settings=settings
            with patch('app.cloudbase_identity.verify_account_channel') as provider:
                rejected=self.renew(token)
            self.assertEqual(rejected.status_code,503);provider.assert_not_called()
        app.state.settings=original
