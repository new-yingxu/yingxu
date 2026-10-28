"""Actual RSA signing/verification, no network and no persistent test keys."""
import time
from pathlib import Path
import sys
import unittest
from uuid import uuid4
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa,ec
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.session_tokens import (validate_signing_configuration,issue_device_session,verify_device_session,public_jwks,
                                SessionTokenConfigurationError,InvalidDeviceSession,DEVICE_SESSION_LIFETIME)


def pem(private):return private.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()


class SessionTokens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.private=pem(cls.key)
        cls.other=pem(rsa.generate_private_key(public_exponent=65537,key_size=2048))
    def setUp(self):
        self.settings=(self.private,'kid-fixture','https://issuer.invalid','yingxu-device')
        now=int(time.time())
        self.claims={'sub':'owner','device_id':'device','origin':'desktop','iss':self.settings[2],'aud':self.settings[3],
                     'iat':now,'nbf':now,'exp':now+900,'jti':str(uuid4())}
    def issue(self,**changes):
        return issue_device_session(*self.settings,**{'owner_id':'owner','device_id':'device','origin':'desktop',**changes})
    def verify(self,token):return verify_device_session(token,*self.settings)
    def signed(self,claims=None,headers=None,key=None):
        return jwt.encode(self.claims if claims is None else claims,key or self.private,algorithm='RS256',headers=headers or {'kid':'kid-fixture','typ':'JWT'})

    def test_actual_rsa_issue_verify_fixed_lifetime_random_jti_and_public_only_jwks(self):
        self.assertIsNone(validate_signing_configuration(*self.settings))
        first=self.issue();second=self.issue()
        verified=self.verify(first)
        self.assertEqual((verified.owner_id,verified.device_id,verified.origin),('owner','device','desktop'))
        self.assertEqual(verified.expires_at-verified.issued_at,DEVICE_SESSION_LIFETIME)
        self.assertNotEqual(verified.jti,self.verify(second).jti)
        self.assertNotIn('PRIVATE KEY',repr(verified));self.assertNotIn(first,repr(verified))
        jwks=public_jwks(self.private,'kid-fixture')
        self.assertEqual(set(jwks['keys'][0]),{'kty','use','alg','kid','n','e'})
        public=jwt.PyJWK.from_dict(jwks['keys'][0]).key
        self.assertEqual(public.public_numbers(),self.key.public_key().public_numbers())
        decoded=jwt.decode(first,public,algorithms=['RS256'],issuer=self.settings[2],audience=self.settings[3])
        self.assertEqual(decoded['exp']-decoded['iat'],900)
        for private_part in ('d','p','q','dp','dq','qi'):self.assertNotIn(private_part,jwks['keys'][0])

    def test_expired_future_nbf_wrong_signature_and_hs_confusion_fail(self):
        now=int(time.time())
        expired={**self.claims,'iat':now-1000,'nbf':now-1000,'exp':now-100}
        future={**self.claims,'iat':now+100,'nbf':now+100,'exp':now+1000}
        tokens=[self.signed(expired),self.signed(future),self.signed(key=self.other),
                jwt.encode(self.claims,'synthetic-hmac-key',algorithm='HS256',headers={'kid':'kid-fixture'})]
        for token in tokens:
            with self.subTest(header=jwt.get_unverified_header(token)),self.assertRaises(InvalidDeviceSession):self.verify(token)

    def test_injected_claims_issuer_audience_identity_lifetime_and_headers_rejected(self):
        cases=[{'roles':['admin']},{'owner_id':'victim'},{'iss':'wrong'},{'aud':['yingxu-device']},{'aud':'wrong'},
               {'sub':''},{'sub':'x'*161},{'device_id':'x'*501},{'device_id':'bad\ndevice'},{'origin':'web'},
               {'exp':self.claims['exp']+1},{'iat':True},{'nbf':self.claims['nbf']-1},{'jti':'fixed'},
               {'jti':str(uuid4()).upper()}]
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaises(InvalidDeviceSession):self.verify(self.signed({**self.claims,**changes}))
        for name in self.claims:
            reduced=dict(self.claims);reduced.pop(name)
            with self.subTest(missing=name),self.assertRaises(InvalidDeviceSession):self.verify(self.signed(reduced))
        for headers in ({'kid':'wrong','typ':'JWT'},{'kid':'kid-fixture','typ':'other'},
                        {'kid':'kid-fixture','typ':'JWT','jku':'https://attacker.invalid/key'}):
            with self.subTest(headers=headers),self.assertRaises(InvalidDeviceSession):self.verify(self.signed(headers=headers))

    def test_bad_configuration_non_rsa_weak_key_and_issuance_injection_rejected(self):
        keys=['bad-private-value',pem(ec.generate_private_key(ec.SECP256R1())),
              pem(rsa.generate_private_key(public_exponent=65537,key_size=1024)),
              self.key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo).decode()]
        for key in keys:
            with self.subTest(key_type=key[:25]),self.assertRaises(SessionTokenConfigurationError) as error:
                validate_signing_configuration(key,*self.settings[1:])
            self.assertNotIn('bad-private-value',str(error.exception))
        for values in [(self.private,'bad\nkid',*self.settings[2:]),(self.private,'kid','',self.settings[3]),(self.private,'kid',self.settings[2],'')]:
            with self.assertRaises(SessionTokenConfigurationError):validate_signing_configuration(*values)
        for changes in ({'owner_id':''},{'device_id':'\x00'},{'origin':'web'}):
            with self.assertRaises(InvalidDeviceSession):self.issue(**changes)
        with self.assertRaises(TypeError):self.issue(roles=['admin'])
        with self.assertRaises(TypeError):self.issue(lifetime=3600)


    def test_real_key_rotation_accepts_old_session_only_while_public_key_retained(self):
        old_token=self.issue()
        previous=public_jwks(self.private,'kid-fixture')
        active=(self.other,'new-active',self.settings[2],self.settings[3])
        self.assertIsNone(validate_signing_configuration(*active,verification_jwks=previous))
        retained=verify_device_session(old_token,*active,verification_jwks=previous)
        self.assertEqual(retained.owner_id,'owner')
        rotated=issue_device_session(*active,owner_id='owner',device_id='device',origin='mobile')
        self.assertEqual(jwt.get_unverified_header(rotated)['kid'],'new-active')
        self.assertEqual(verify_device_session(rotated,*active,verification_jwks=previous).origin,'mobile')
        public=public_jwks(active[0],active[1],verification_jwks=previous)
        self.assertEqual([k['kid'] for k in public['keys']],['new-active','kid-fixture'])
        for item in public['keys']:
            self.assertEqual(set(item),{'kty','use','alg','kid','n','e'})
        # Same process; no cache keeps the removed key usable.
        with self.assertRaises(InvalidDeviceSession):verify_device_session(old_token,*active)
        with self.assertRaises(InvalidDeviceSession):verify_device_session(old_token,*active,verification_jwks={'keys':[]})
        self.assertEqual(verify_device_session(rotated,*active).owner_id,'owner')

    def test_rotation_selects_exact_kid_and_rejects_wrong_signature_hs_and_headers(self):
        previous=public_jwks(self.private,'kid-fixture')
        active=(self.other,'new-active',self.settings[2],self.settings[3])
        wrong_key=self.signed(headers={'kid':'kid-fixture','typ':'JWT'},key=self.other)
        unknown=self.signed(headers={'kid':'unknown','typ':'JWT'})
        hs=jwt.encode(self.claims,'fixture-hmac-key',algorithm='HS256',headers={'kid':'kid-fixture'})
        injected=self.signed(headers={'kid':'kid-fixture','typ':'JWT','jwk':previous['keys'][0]})
        for token in (wrong_key,unknown,hs,injected):
            with self.assertRaises(InvalidDeviceSession):verify_device_session(token,*active,verification_jwks=previous)

    def test_verification_jwks_rejects_private_url_extra_fields_duplicate_and_overflow(self):
        import copy
        old=public_jwks(self.other,'old')['keys'][0]
        cases=[{'keys':[dict(old,d='private')]},{'keys':[dict(old,p='private')]},
               {'keys':[dict(old,jku='https://remote.invalid/key')]},{'keys':[dict(old,x5u='https://remote.invalid/cert')]},
               {'keys':[dict(old,extra='ignored')]},{'keys':[dict(old,kid='kid-fixture')]},
               {'keys':[old,copy.deepcopy(old)]},{'keys':[dict(old,kid=str(i)) for i in range(4)]},
               {'keys':[old],'url':'https://remote.invalid'},{'keys':'invalid'},[],
               {'keys':[dict(old,alg='HS256')]},{'keys':[dict(old,alg='RS512')]},
               {'keys':[dict(old,kty='EC')]},{'keys':[dict(old,use='enc')]},
               {'keys':[dict(old,n='AQAB=')]},{'keys':[dict(old,e='AAEAAQ')]},
               {'keys':[dict(old,n='AQAB')]},{'keys':[dict(old,kid='bad\nkid')]}]
        weak=rsa.generate_private_key(public_exponent=65537,key_size=1024).public_key().public_numbers()
        from app.session_tokens import _encoded_uint
        cases.append({'keys':[dict(old,n=_encoded_uint(weak.n),e=_encoded_uint(weak.e))]})
        for value in cases:
            with self.subTest(shape=str(value)[:80]):
                with self.assertRaises(SessionTokenConfigurationError):validate_signing_configuration(*self.settings,verification_jwks=value)
                with self.assertRaises(SessionTokenConfigurationError):public_jwks(self.private,'kid-fixture',verification_jwks=value)
                with self.assertRaises(SessionTokenConfigurationError):verify_device_session(self.issue(),*self.settings,verification_jwks=value)

    def test_three_extra_public_keys_supported_and_config_never_mutated(self):
        import copy
        public=public_jwks(self.other,'old')['keys'][0]
        previous={'keys':[dict(public,kid=f'old-{i}') for i in range(3)]}
        before=copy.deepcopy(previous)
        validate_signing_configuration(*self.settings,verification_jwks=previous)
        result=public_jwks(self.private,'kid-fixture',verification_jwks=previous)
        self.assertEqual(len(result['keys']),4);self.assertEqual(previous,before)
        result['keys'][1]['kid']='mutated-output'
        self.assertEqual(previous,before)


if __name__=='__main__':unittest.main()
