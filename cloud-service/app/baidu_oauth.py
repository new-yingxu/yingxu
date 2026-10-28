"""Baidu confidential-client boundary. No file bytes or durable provider tokens.

Protocol references: https://pan.baidu.com/union/doc/al0rwqzzl and
baidu-netdisk/baidu-drive-sdk-go/baidudriver/api/auth_{code2token,refresh_token}.go.
Only the application Secret lives here; user token pairs go to native keyrings.
"""
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler

CALLBACK = 'http://127.0.0.1:8765/callback'
TOKEN_ENDPOINT = 'https://openapi.baidu.com/oauth/2.0/token'
MAX_RESPONSE = 65536


class BaiduOAuthError(Exception):
    def __init__(self, code, status=502):
        self.code, self.status = code, status
        super().__init__(code)


def configuration(settings):
    client_id = settings.baidu_client_id or ''
    secret = settings.baidu_client_secret or ''
    folder = settings.baidu_app_folder or ''
    if (not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', client_id)
            or not 8 <= len(secret) <= 512
            or any(c.isspace() for c in secret)
            or not folder.startswith('/apps/') or folder.count('/') != 2
            or folder.rsplit('/', 1)[-1] in ('', '.', '..')
            or any(ord(c) < 32 or c in '\\?#' for c in folder)):
        raise BaiduOAuthError('BAIDU_NOT_CONFIGURED', 503)
    return {'clientId': client_id, 'redirectUri': CALLBACK,
            'appFolder': folder, 'scopes': ['basic', 'netdisk']}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange(settings, grant_type, credential):
    config = configuration(settings)
    params = {'client_id': config['clientId'], 'client_secret': settings.baidu_client_secret,
              'grant_type': grant_type}
    if grant_type == 'authorization_code':
        params.update(code=credential, redirect_uri=CALLBACK)
    elif grant_type == 'refresh_token':
        params['refresh_token'] = credential
    else:
        raise BaiduOAuthError('BAIDU_INVALID_GRANT', 422)
    request = Request(TOKEN_ENDPOINT + '?' + urlencode(params),
                      headers={'Accept': 'application/json', 'User-Agent': 'Yingxu/0.9'})
    opener = build_opener(ProxyHandler({}), NoRedirect(),
                          HTTPSHandler(context=ssl.create_default_context()))
    try:
        # Deliberately one attempt: authorization codes / rotated refresh tokens
        # must not be automatically replayed after an ambiguous network outcome.
        with opener.open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE:
                raise ValueError()
            value = json.loads(raw)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise BaiduOAuthError('BAIDU_TOKEN_OUTCOME_UNCERTAIN') from None
    if not isinstance(value, dict):
        raise BaiduOAuthError('BAIDU_INVALID_RESPONSE')
    if value.get('error'):
        raise BaiduOAuthError('BAIDU_AUTHORIZATION_REJECTED', 400)
    scopes = set(re.split(r'[,\s]+', str(value.get('scope', ''))))
    if 'netdisk' not in scopes:
        raise BaiduOAuthError('BAIDU_NETDISK_SCOPE_REQUIRED', 403)
    for name in ('access_token', 'refresh_token'):
        token = value.get(name)
        if not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(c.isspace() for c in token):
            raise BaiduOAuthError('BAIDU_INVALID_RESPONSE')
    expiry = value.get('expires_in')
    if type(expiry) is not int or not 1 <= expiry <= 366 * 86400:
        raise BaiduOAuthError('BAIDU_INVALID_RESPONSE')
    return {name: value[name] for name in ('access_token', 'refresh_token', 'expires_in', 'scope')}
