"""Real signature checks with synthetic, in-memory issuer keys; no cloud traffic."""
import json
from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from app import auth
from app.config import Settings


class JWTIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.settings = Settings('development', 'sqlite://', 'jwt', None,
                                 'https://issuer.invalid', 'yingxu-test', 'https://issuer.invalid/jwks', (), 'test')
        self.claims = dict(sub='owner-fixture', device_id='device-fixture', origin='desktop',
                           iss=self.settings.jwt_issuer, aud=self.settings.jwt_audience, exp=int(time.time())+600)
        auth._jwks_clients.clear()

    def tearDown(self):
        auth._jwks_clients.clear()

    def token(self, claims=None, key=None):
        return jwt.encode(claims if claims is not None else self.claims, key or self.key,
                          algorithm='RS256', headers={'kid':'fixture'})

    def test_signed_claims_and_invalid_identity(self):
        client = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.key.public_key()))
        auth._jwks_clients[self.settings.jwt_jwks_url] = client
        self.assertEqual(auth.principal_from_token(self.token(),self.settings),
                         auth.Principal('owner-fixture','device-fixture','desktop'))
        for changes in ({'exp':0},{'iss':'https://wrong.invalid'},{'aud':'wrong'},
                        {'origin':'admin'},{'device_id':''},{'sub':'x'*161},{'device_id':'x'*501}):
            with self.subTest(changes=changes), self.assertRaises((ValueError,jwt.PyJWTError)):
                auth.principal_from_token(self.token({**self.claims,**changes}),self.settings)
        for missing in ('sub','iss','aud','exp','device_id','origin'):
            claims=dict(self.claims)
            del claims[missing]
            with self.subTest(missing=missing), self.assertRaises((ValueError,jwt.PyJWTError)):
                auth.principal_from_token(self.token(claims),self.settings)
        with self.assertRaises(jwt.InvalidSignatureError):
            auth.principal_from_token(self.token(key=self.other_key),self.settings)
        forged=jwt.encode(self.claims,'synthetic-secret',algorithm='HS256',headers={'kid':'fixture'})
        with self.assertRaises(jwt.InvalidAlgorithmError):
            auth.principal_from_token(forged,self.settings)

    def test_removed_key_is_rejected_after_bounded_key_set_cache(self):
        # Exercise PyJWT's real set cache and selection. Only network fetch is
        # replaced; a removed kid must not survive an unbounded per-key cache.
        client=jwt.PyJWKClient(self.settings.jwt_jwks_url,cache_keys=False,cache_jwk_set=True,lifespan=300,timeout=5)
        current=[self.key]
        def fetch():
            keys=[]
            for key in current:
                value=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
                value.update(kid='fixture' if key is self.key else 'replacement',use='sig')
                keys.append(value)
            result={'keys':keys}
            client.jwk_set_cache.put(result)
            return result
        with patch('app.auth.PyJWKClient',return_value=client) as constructor, patch.object(client,'fetch_data',side_effect=fetch), patch('time.monotonic',return_value=1000):
            token=self.token()
            auth.principal_from_token(token,self.settings)
            constructor.assert_called_once_with(self.settings.jwt_jwks_url,cache_keys=False,cache_jwk_set=True,lifespan=300,timeout=5)
            current[:]=[self.other_key]
            auth.principal_from_token(token,self.settings)  # within bounded cache
            with patch('time.monotonic',return_value=1301), self.assertRaises(jwt.PyJWKClientError):
                auth.principal_from_token(token,self.settings)
