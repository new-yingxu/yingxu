"""Server-issued device sessions, RS256 only. No network, profiles or key storage.

A session binds a verified account/client channel to a server-registered device
identifier. It is not hardware attestation; live revocation stays in the store.
"""
import base64
from dataclasses import dataclass
import re
import time
from uuid import UUID,uuid4
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey,RSAPublicNumbers

DEVICE_SESSION_LIFETIME = 900


class SessionTokenConfigurationError(ValueError):
    pass


class InvalidDeviceSession(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedDeviceSession:
    owner_id:str
    device_id:str
    origin:str
    jti:str
    issued_at:int
    expires_at:int


def _key(private_key_pem):
    try:
        if isinstance(private_key_pem,str):private_key_pem=private_key_pem.encode('ascii')
        if not isinstance(private_key_pem,bytes) or not 1<=len(private_key_pem)<=65536:raise ValueError()
        private=load_pem_private_key(private_key_pem,password=None)
        if not isinstance(private,RSAPrivateKey) or private.key_size<2048:raise ValueError()
        return private
    except (ValueError,TypeError,UnicodeError,UnsupportedAlgorithm):
        raise SessionTokenConfigurationError('a valid RSA private signing key of at least 2048 bits is required') from None


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 or ord(c)==127 for c in value)


def _configuration(private_key_pem,kid,issuer,audience):
    if (not isinstance(kid,str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',kid) is None
        or not _label(issuer,500) or not _label(audience,500)):
        raise SessionTokenConfigurationError('signing kid, issuer and audience must be configured')
    return _key(private_key_pem)


def validate_signing_configuration(private_key_pem,kid,issuer,audience,verification_jwks=None):
    private=_configuration(private_key_pem,kid,issuer,audience)
    _verification_keys(private,kid,verification_jwks)


def issue_device_session(private_key_pem,kid,issuer,audience,*,owner_id,device_id,origin):
    private=_configuration(private_key_pem,kid,issuer,audience)
    if not _label(owner_id,160) or not _label(device_id,500) or not isinstance(origin,str) or origin not in {'desktop','mobile'}:
        raise InvalidDeviceSession('invalid device session identity')
    now=int(time.time())
    claims={'sub':owner_id,'device_id':device_id,'origin':origin,'iss':issuer,'aud':audience,
            'iat':now,'nbf':now,'exp':now+DEVICE_SESSION_LIFETIME,'jti':str(uuid4())}
    return jwt.encode(claims,private,algorithm='RS256',headers={'kid':kid,'typ':'JWT'})


def verify_device_session(token,private_key_pem,kid,issuer,audience,verification_jwks=None):
    private=_configuration(private_key_pem,kid,issuer,audience)
    keys=_verification_keys(private,kid,verification_jwks)
    try:
        if not isinstance(token,str) or not 1<=len(token)<=16384:raise ValueError()
        header=jwt.get_unverified_header(token)
        if set(header)!={'alg','kid','typ'} or header['alg']!='RS256' or not isinstance(header['kid'],str) or header['kid'] not in keys or header['typ']!='JWT':raise ValueError()
        required={'sub','device_id','origin','iss','aud','iat','nbf','exp','jti'}
        claims=jwt.decode(token,keys[header['kid']],algorithms=['RS256'],issuer=issuer,audience=audience,
                          options={'require':list(required),'strict_aud':True})
        if set(claims)!=required:raise ValueError()
        owner,device,origin=claims['sub'],claims['device_id'],claims['origin']
        if not _label(owner,160) or not _label(device,500) or not isinstance(origin,str) or origin not in {'desktop','mobile'}:raise ValueError()
        if any(not isinstance(claims[k],int) or isinstance(claims[k],bool) for k in ('iat','nbf','exp')):raise ValueError()
        if claims['nbf']!=claims['iat'] or claims['exp']-claims['iat']!=DEVICE_SESSION_LIFETIME:raise ValueError()
        jti=claims['jti']
        if not isinstance(jti,str) or len(jti)!=36 or str(UUID(jti))!=jti or UUID(jti).version!=4:raise ValueError()
        return VerifiedDeviceSession(owner,device,origin,jti,claims['iat'],claims['exp'])
    except (jwt.PyJWTError,ValueError,TypeError,KeyError,AttributeError):
        raise InvalidDeviceSession('invalid or expired device session') from None


def _encoded_uint(number):
    raw=number.to_bytes((number.bit_length()+7)//8,'big')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _public_jwk(public,kid):
    numbers=public.public_numbers()
    return {'kty':'RSA','use':'sig','alg':'RS256','kid':kid,'n':_encoded_uint(numbers.n),'e':_encoded_uint(numbers.e)}


def _decoded_uint(value):
    if not isinstance(value,str) or not 1<=len(value)<=4096 or re.fullmatch(r'[A-Za-z0-9_-]+',value) is None:
        raise ValueError()
    raw=base64.b64decode(value+'='*((-len(value))%4),altchars=b'-_',validate=True)
    number=int.from_bytes(raw,'big')
    if number==0 or _encoded_uint(number)!=value:raise ValueError()
    return number


def _verification_keys(private,kid,verification_jwks):
    """No remote lookup/cache: removing an old public key revokes it at once."""
    keys={kid:private.public_key()}
    if verification_jwks is None:return keys
    try:
        if not isinstance(verification_jwks,dict) or set(verification_jwks)!={'keys'}:raise ValueError()
        old=verification_jwks['keys']
        if not isinstance(old,list) or len(old)>3:raise ValueError()
        for item in old:
            if not isinstance(item,dict) or set(item)!={'kty','use','alg','kid','n','e'}:raise ValueError()
            old_kid=item['kid']
            if (not isinstance(old_kid,str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',old_kid) is None
                or old_kid in keys or item['kty']!='RSA' or item['use']!='sig' or item['alg']!='RS256'):
                raise ValueError()
            n,e=_decoded_uint(item['n']),_decoded_uint(item['e'])
            if n.bit_length()<2048:raise ValueError()
            keys[old_kid]=RSAPublicNumbers(e,n).public_key()
        return keys
    except (ValueError,TypeError,KeyError,UnsupportedAlgorithm):
        raise SessionTokenConfigurationError('verification JWKS must contain at most three unique RS256 RSA public keys') from None


def public_jwks(private_key_pem,kid,verification_jwks=None):
    if not isinstance(kid,str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',kid) is None:
        raise SessionTokenConfigurationError('invalid signing kid')
    keys=_verification_keys(_key(private_key_pem),kid,verification_jwks)
    return {'keys':[_public_jwk(public,key_id) for key_id,public in keys.items()]}
