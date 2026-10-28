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


def verify_access_token(env_id,access_token,client_id=None):
    if not isinstance(env_id,str) or not env_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in env_id):
        raise ValueError('invalid CloudBase environment')
    if not isinstance(access_token,str) or not access_token or len(access_token)>16384 or any(c in access_token for c in '\r\n'):
        raise InvalidCloudBaseIdentity('invalid account token')
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
        if (not isinstance(owner,str) or not owner.strip() or len(owner)>160
            or profile.get('status')!='ACTIVE' or profile.get('type')!='external'
            or profile.get('internal_user_type')!='generalUser'):
            raise InvalidCloudBaseIdentity('account is not an active external user')
        providers=profile.get('providers',[])
        if not isinstance(providers,list):raise ValueError()
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


def verify_account_channel(env_id,access_token,client_origins:dict):
    """Bind user/me identity to same-environment introspection and client map.

    Official contract: https://docs.cloudbase.net/http-api/auth/auth-token-introspect
    No caller device/origin/client headers participate in this decision.
    """
    if not isinstance(client_origins,dict) or not client_origins:
        raise ValueError('configured client origins are required')
    # Copy the server configuration once; never infer origin from a request.
    clients=dict(client_origins)
    if any(not isinstance(client,str) or not 1<=len(client)<=500 or any(ord(c)<33 for c in client)
           or not isinstance(origin,str) or origin not in {'desktop','mobile'} for client,origin in clients.items()):
        raise ValueError('invalid configured client origins')
    account=verify_access_token(env_id,access_token)
    request=Request(f'https://{env_id}.api.tcloudbasegateway.com/auth/v1/token/introspect',method='GET',
                    headers={'Authorization':'Bearer '+access_token,'Accept':'application/json'})
    try:
        with build_opener(NoIdentityRedirect()).open(request,timeout=5) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError()
        token_info=json.loads(raw)
        if not isinstance(token_info,dict):raise ValueError()
        client_id=token_info.get('client_id')
        if (token_info.get('sub')!=account.owner_id or token_info.get('token_type')!='Bearer'
            or not isinstance(client_id,str) or client_id not in clients
            or ('active' in token_info and token_info['active'] is not True)):
            raise InvalidCloudBaseIdentity('account token channel is not accepted')
        return VerifiedCloudBaseChannel(account.owner_id,clients[client_id],client_id)
    except HTTPError as exc:
        code=exc.code
        exc.close()
        if code in (401,403):raise InvalidCloudBaseIdentity('invalid account token') from None
        raise CloudBaseIdentityUnavailable('CloudBase token introspection unavailable') from None
    except InvalidCloudBaseIdentity:
        raise
    except (URLError,TimeoutError,ValueError,TypeError,AttributeError):
        raise CloudBaseIdentityUnavailable('CloudBase token introspection unavailable or invalid response') from None
