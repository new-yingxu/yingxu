"""Baidu broker boundary: no real accounts, mail, provider calls or keys."""
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch, MagicMock
import test_graph_checkpoint_http as fixtures
from app import baidu_oauth
from app.config import Settings
from app.main import app
from app.auth import Principal, require_principal


class BaiduOAuthHTTP(unittest.TestCase):
    setUp = fixtures.GraphCheckpointHTTP.setUp
    tearDown = fixtures.GraphCheckpointHTTP.tearDown

    def configured(self):
        app.state.settings = replace(app.state.settings, baidu_client_id='synthetic-app-key',
            baidu_client_secret='synthetic-secret-not-real', baidu_app_folder='/apps/影序测试')

    def test_configuration_guard_device_origin_and_no_secret_response(self):
        url = '/v1/netdisk/baidu/config'
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 503)
        self.configured()
        reply = self.client.get(url, headers=self.headers)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertNotIn('synthetic-secret', reply.text)
        self.assertEqual(reply.json()['redirectUri'], baidu_oauth.CALLBACK)
        self.assertEqual(reply.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get(url).status_code, 401)
        app.dependency_overrides[require_principal] = lambda: Principal(owner_id='local-dev-owner', device_id='dev-device', origin='mobile')
        try:
            self.assertEqual(self.client.get(url, headers=self.headers).status_code, 403)
        finally:
            app.dependency_overrides.clear()

    def test_token_input_failures_do_not_echo_credential(self):
        self.configured()
        for body in [dict(grant_type='bad', credential='sensitive-test-value'),
                     dict(grant_type='refresh_token', credential='sensitive-test-value', redirect_uri='https://evil.invalid'),
                     dict(grant_type='refresh_token', credential='sensitive test value')]:
            response = self.client.post('/v1/netdisk/baidu/token', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('sensitive', response.text)

    def test_authenticated_exchange_only_once_and_only_allowlisted_response(self):
        self.configured()
        value = dict(access_token='synthetic-access', refresh_token='synthetic-refresh', expires_in=3600, scope='basic netdisk')
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({**value, 'session_secret':'never-return'}).encode()
        response.__enter__.return_value = response
        opener = MagicMock(); opener.open.return_value = response
        with patch('app.baidu_oauth.build_opener', return_value=opener):
            result = self.client.post('/v1/netdisk/baidu/token', json={'grant_type':'authorization_code','credential':'synthetic-code'}, headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json(), value)
        self.assertEqual(result.headers['cache-control'], 'no-store')
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(baidu_oauth.TOKEN_ENDPOINT+'?'))
        self.assertIn('redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback',request.full_url)
        self.assertNotIn('never-return',result.text)

    def test_scope_denial_ambiguous_timeout_and_revoked_device(self):
        self.configured()
        response=MagicMock();response.status=200;response.__enter__.return_value=response
        opener=MagicMock();opener.open.return_value=response
        payload={'grant_type':'refresh_token','credential':'synthetic-refresh'}
        response.read.return_value=json.dumps({'access_token':'x','refresh_token':'y','scope':'basic','expires_in':3600}).encode()
        with patch('app.baidu_oauth.build_opener', return_value=opener):
            denied=self.client.post('/v1/netdisk/baidu/token',json=payload,headers=self.headers)
            self.assertEqual(denied.status_code,403)
            opener.open.side_effect=TimeoutError('sensitive-upstream-url')
            timeout=self.client.post('/v1/netdisk/baidu/token',json=payload,headers=self.headers)
            self.assertEqual(timeout.status_code,502)
            self.assertNotIn('sensitive',timeout.text)
            self.assertEqual(opener.open.call_count,2)
        with patch('app.main._device',side_effect=__import__('fastapi').HTTPException(403)),patch('app.baidu_oauth.exchange') as exchange:
            self.assertEqual(self.client.post('/v1/netdisk/baidu/token',json=payload,headers=self.headers).status_code,403)
            exchange.assert_not_called()

    def test_secret_repr_and_folder_traversal(self):
        self.configured()
        self.assertNotIn('synthetic-secret',repr(app.state.settings))
        for folder in ('/apps/..','/apps/name/extra','/apps/name?token=bad','/arbitrary'):
            with self.assertRaises(baidu_oauth.BaiduOAuthError):
                baidu_oauth.configuration(replace(app.state.settings,baidu_app_folder=folder))
