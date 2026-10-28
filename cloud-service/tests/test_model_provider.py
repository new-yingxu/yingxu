import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.model_provider import credential_for_owner,generate_answer,local_ollama_model_state,_tool_provider_request,ModelNotConfigured,ModelUnavailable,NoRedirect
from urllib.error import HTTPError,URLError
from urllib.request import Request


class ModelProvider(unittest.TestCase):
    def setUp(self):
        self.secret=json.dumps({'owner-a':{'apiKey':'synthetic-key','model':'qwen-plus','region':'beijing'}})
        self.credential=credential_for_owner(self.secret,'owner-a')

    def test_credential_owner_isolation_and_no_secret_repr(self):
        self.assertNotIn('synthetic-key',repr(self.credential))
        for secret,owner in ((self.secret,'owner-b'),('[]','owner-a'),('{','owner-a'),(json.dumps({'owner-a':{'apiKey':'key','region':'http://localhost'}}),'owner-a')):
            with self.assertRaises(ModelNotConfigured):credential_for_owner(secret,owner)

    def test_ollama_is_local_development_only(self):
        secret=json.dumps({'owner-a':{'provider':'ollama','model':'qwen2.5:7b'}})
        with self.assertRaises(ModelNotConfigured):credential_for_owner(secret,'owner-a')
        credential=credential_for_owner(secret,'owner-a',allow_local=True)
        self.assertEqual(credential.provider,'ollama')
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':'local'}}]}).encode()
        with patch('app.model_provider.build_opener',return_value=opener) as factory:
            self.assertEqual(generate_answer(credential,'query',[])['provider'],'ollama')
        self.assertEqual(factory.call_args.args[0].proxies,{})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:11434/v1/chat/completions')
        self.assertNotIn('Authorization',request.headers)
        # Fixed loopback requests must never inherit a system HTTP proxy.
        with self.assertRaises(ModelNotConfigured):credential_for_owner(json.dumps({'owner-a':{'provider':'ollama','model':'qwen:cloud'}}),'owner-a',allow_local=True)

    def test_accountless_deepseek_byok_uses_fixed_official_endpoint(self):
        credential=credential_for_owner(json.dumps({'local-desktop-owner':{'provider':'deepseek','model':'deepseek-flash','region':'global','apiKey':'synthetic-deepseek-key'}}),'local-desktop-owner',allow_local=True)
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':'synthetic'}}]}).encode()
        with patch('app.model_provider.build_opener',return_value=opener):
            result=generate_answer(credential,'synthetic query',[])
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.deepseek.com/chat/completions')
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-deepseek-key')
        self.assertEqual(json.loads(request.data)['model'],'deepseek-flash')
        self.assertEqual(json.loads(request.data)['thinking'],{'type':'disabled'})
        self.assertEqual(result['provider'],'deepseek')
        self.assertNotIn('synthetic-deepseek-key',repr(credential))

    def test_tool_request_uses_provider_specific_non_reasoning_options(self):
        deepseek=credential_for_owner(json.dumps({'local-desktop-owner':{'provider':'deepseek','model':'deepseek-flash','apiKey':'synthetic-key'}}),'local-desktop-owner',allow_local=True)
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value=b'{"choices":[]}'
        with patch('app.model_provider.build_opener',return_value=opener):
            _tool_provider_request(deepseek,[{'role':'user','content':'synthetic'}])
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertNotIn('enable_thinking',payload)

        with patch('app.model_provider.build_opener',return_value=opener):
            _tool_provider_request(self.credential,[{'role':'user','content':'synthetic'}])
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertIs(payload['enable_thinking'],False)
        self.assertNotIn('thinking',payload)

    def test_desktop_byok_rejects_custom_endpoints_and_unknown_provider(self):
        for value in (
            {'provider':'deepseek','model':'deepseek-flash','region':'https://attacker.invalid','apiKey':'fixture'},
            {'provider':'custom','model':'model','apiKey':'fixture'},
            {'provider':'dashscope','model':'../bad','apiKey':'fixture'},
        ):
            with self.assertRaises(ModelNotConfigured):credential_for_owner(json.dumps({'owner':value}),'owner',allow_local=True)

    def test_local_model_status_probes_only_fixed_loopback_catalog(self):
        credential=credential_for_owner(json.dumps({'owner-a':{'provider':'ollama','model':'qwen2.5:7b'}}),'owner-a',allow_local=True)
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'models':[{'name':'qwen2.5:7b','model':'qwen2.5:7b'}]}).encode()
        with patch('app.model_provider.build_opener',return_value=opener) as factory:
            self.assertEqual(local_ollama_model_state(credential),'ready')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:11434/api/tags')
        self.assertEqual(opener.open.call_args.kwargs['timeout'],2)
        self.assertEqual(factory.call_args.args[0].proxies,{})
        opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'models':[{'name':'other:latest'}]}).encode()
        with patch('app.model_provider.build_opener',return_value=opener):
            self.assertEqual(local_ollama_model_state(credential),'model_not_installed')
        opener.open.side_effect=URLError('local service unavailable')
        with patch('app.model_provider.build_opener',return_value=opener):
            self.assertEqual(local_ollama_model_state(credential),'ollama_unavailable')

    def test_transport_minimizes_context_and_reports_real_usage(self):
        opener=MagicMock()
        response=opener.open.return_value.__enter__.return_value
        response.read.return_value=json.dumps({'choices':[{'message':{'content':'Synthetic answer'}}],'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}).encode()
        with patch('app.model_provider.build_opener',return_value=opener):
            result=generate_answer(self.credential,'备份状态',[{'jobId':'job','name':'Synthetic','state':'success','paths':['/private/path'],'source':'/private/source','gps':'private'}])
        request=opener.open.call_args.args[0]
        payload=json.loads(request.data)
        self.assertNotIn('/private',request.data.decode())
        self.assertNotIn('gps',request.data.decode())
        self.assertNotIn('tools',payload)
        self.assertEqual(opener.open.call_args.kwargs['timeout'],15)
        self.assertEqual(result['usage']['total_tokens'],5)
        self.assertFalse(result['executionAuthorized'])

    def test_rejects_tool_calls_and_invalid_answers(self):
        for message in ({'content':'answer','tool_calls':[{'function':{'name':'delete'}}]}, {'content':''}, {'content':{'unexpected':True}}):
            opener=MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':message}]}).encode()
            with patch('app.model_provider.build_opener',return_value=opener),self.assertRaises(ModelUnavailable):
                generate_answer(self.credential,'query',[])

    def test_redirect_cannot_forward_key(self):
        with self.assertRaises(HTTPError) as error:
            NoRedirect().redirect_request(Request('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'),None,302,'redirect',{},'http://localhost/')
        error.exception.close()

