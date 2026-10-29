"""CloudBase access-token verification boundary, not a device-session issuer.

Official API: https://docs.cloudbase.net/http-api/auth/user-me
The optional x-device-id is client generated; it is not proof of device/origin.
Never turn a verified account plus request headers into an app Principal.
"""
from dataclasses import dataclass
import json
from urllib.parse import urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError


class InvalidCloudBaseIdentity(ValueError):
    pass


class CloudBaseIdentityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedCloudBaseAccount:
    owner_id:str
    # Deliberately no device_id, origin, email, phone, role or provider token.


class NoIdentityRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise HTTPError(req.full_url,code,'identity redirect refused',headers,fp)


def _validate_request(env_id,access_token):
    if not isinstance(env_id,str) or not env_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in env_id):
        raise ValueError('invalid CloudBase environment')
    if not isinstance(access_token,str) or not access_token or len(access_token)>16384 or any(c in access_token for c in '\r\n'):
        raise InvalidCloudBaseIdentity('invalid account token')


def _has_verified_credential(profile):
    """账号已在 CloudBase 绑定可自助验证的凭据（不读取任何凭据值）。"""
    if profile.get('has_password') is True or bool(profile.get('password')):
        return True
    for field in ('email','phone_number','username','user_id'):
        value=profile.get(field)
        if isinstance(value,str) and value.strip():return True
    providers=profile.get('providers')
    if isinstance(providers,list) and any(isinstance(p,dict) and p.get('id') not in (None,'','anonymous','anon') for p in providers):
        return True
    return False


def verify_access_token(env_id,access_token,client_id=None):
    _validate_request(env_id,access_token)
    if client_id is not None and (not isinstance(client_id,str) or not 1<=len(client_id)<=500):
        raise ValueError('invalid configured client')
    url=f'https://{env_id}.api.tcloudbasegateway.com/auth/v1/user/me'
    if client_id:url+='?'+urlencode({'client_id':client_id})
    request=Request(url,method='GET',headers={'Authorization':'Bearer '+access_token,'Accept':'application/json'})
    try:
        with build_opener(NoIdentityRedirect()).open(request,timeout=5) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError()
        profile=json.loads(raw)
        owner=profile.get('sub')
        if not isinstance(owner,str) or not owner.strip() or len(owner)>160:
            raise InvalidCloudBaseIdentity('account profile has no usable subject')
        if 'status' in profile:
            # 有状态字段时以它为准；封禁/待激活一律拒绝。
            if profile.get('status')!='ACTIVE':
                raise InvalidCloudBaseIdentity('account status is not ACTIVE')
        else:
            # 实测：邮箱注册的 external 账户 profile 不带 status/internal_user_type/providers，
            # 管理端 API 对同一账户报告 UserStatus=ACTIVE。这里不做"缺失即激活"的假定，
            # 而是要求外部类型 + 已绑定可自助验证的凭据；能拿到刚签发的令牌本身也要求
            # CloudBase 认证成功（封禁账号无法登录换取令牌）。
            if not _has_verified_credential(profile):
                raise InvalidCloudBaseIdentity('account status unavailable and no verified credential')
        if profile.get('type')!='external':
            raise InvalidCloudBaseIdentity('account is not an external user')
        if 'internal_user_type' in profile and profile.get('internal_user_type')!='generalUser':
            raise InvalidCloudBaseIdentity('account is not a general user')
        providers=profile.get('providers',[])
        if not isinstance(providers,list):
            raise InvalidCloudBaseIdentity('account profile has no provider list')
        if any(not isinstance(p,dict) or p.get('id') in ('anonymous','anon') for p in providers):
            raise InvalidCloudBaseIdentity('anonymous identity is not accepted')
        return VerifiedCloudBaseAccount(owner)
    except HTTPError as exc:
        code=exc.code
        exc.close()
        if code in (401,403):raise InvalidCloudBaseIdentity('invalid account token') from None
        raise CloudBaseIdentityUnavailable('CloudBase identity provider unavailable') from None
    except InvalidCloudBaseIdentity:
        raise
    except (URLError,TimeoutError,ValueError,TypeError,AttributeError):
        raise CloudBaseIdentityUnavailable('CloudBase identity provider unavailable or invalid response') from None


@dataclass(frozen=True)
class VerifiedCloudBaseChannel:
    """Verified account and server-mapped OAuth client; no hardware attestation."""
    owner_id:str
    origin:str
    client_id:str


def _introspect_token(env_id,access_token):
    """Resolve the token's own client before asking for its profile.

    /auth/v1/user/me only returns status/internal_user_type/providers when the
    request carries the client_id the token was issued for; without it the
    gateway answers as the bare environment client and omits those fields.
    Introspection is the server-side source of that client_id, so the profile
    request is always scoped to the token's real client.
    """
    request=Request(f'https://{env_id}.api.tcloudbasegateway.com/auth/v1/token/introspect',method='GET',
                    headers={'Authorization':'Bearer '+access_token,'Accept':'application/json'})
    try:
        with build_opener(NoIdentityRedirect()).open(request,timeout=5) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError()
        token_info=json.loads(raw)
        if not isinstance(token_info,dict):raise ValueError()
        return token_info
    except HTTPError as exc:
        code=exc.code
        exc.close()
        if code in (401,403):raise InvalidCloudBaseIdentity('invalid account token') from None
        raise CloudBaseIdentityUnavailable('CloudBase token introspection unavailable') from None
    except (URLError,TimeoutError,ValueError,TypeError,AttributeError):
        raise CloudBaseIdentityUnavailable('CloudBase token introspection unavailable or invalid response') from None


def verify_account_channel(env_id,access_token,client_origins:dict):
    """Bind user/me identity to same-environment introspection and client map.

    Official contracts: https://docs.cloudbase.net/http-api/auth/auth-token-introspect
    and https://docs.cloudbase.net/http-api/auth/user-me
    No caller device/origin/client headers participate in this decision.
    """
    if not isinstance(client_origins,dict) or not client_origins:
        raise ValueError('configured client origins are required')
    # Copy the server configuration once; never infer origin from a request.
    clients=dict(client_origins)
    if any(not isinstance(client,str) or not 1<=len(client)<=500 or any(ord(c)<33 for c in client)
           or not isinstance(origin,str) or origin not in {'desktop','mobile'} for client,origin in clients.items()):
        raise ValueError('invalid configured client origins')
    _validate_request(env_id,access_token)
    token_info=_introspect_token(env_id,access_token)
    client_id=token_info.get('client_id')
    if (not isinstance(token_info.get('sub'),str) or not token_info['sub']
        or token_info.get('token_type')!='Bearer'
        or ('active' in token_info and token_info['active'] is not True)):
        raise InvalidCloudBaseIdentity('token introspection rejected')
    if not isinstance(client_id,str) or not client_id:
        raise InvalidCloudBaseIdentity('token introspection has no client')
    if client_id not in clients:
        raise InvalidCloudBaseIdentity('token client is not registered for this environment')
    # The profile request must carry the token's own client; missing status,
    # type or internal_user_type stays a closed failure, never "assume active".
    account=verify_access_token(env_id,access_token,client_id)
    if account.owner_id!=token_info['sub']:
        raise InvalidCloudBaseIdentity('profile subject differs from token subject')
    return VerifiedCloudBaseChannel(account.owner_id,clients[client_id],client_id)
