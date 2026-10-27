"""Strict handoff projections and actual RSA continuation signatures; no network."""
from pathlib import Path
import sys,json,time,copy,unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.local_tool_protocol import (LocalTurnRequest,LocalToolResult,ContinuationRequest,validate_arguments,
    issue_local_state,verify_local_state,InvalidLocalState,LocalStateExpired,STATE_TYP)
from app.session_tokens import issue_device_session,verify_device_session,InvalidDeviceSession,public_jwks,SessionTokenConfigurationError


def private_pem():return rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()


class LocalToolProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.private=private_pem();cls.other=private_pem()
    def setUp(self):
        self.settings=(self.private,'local-key','https://issuer.invalid','yingxu-app')
        self.principal=SimpleNamespace(owner_id='owner',device_id='device',origin='desktop')
        self.state={'owner_id':'owner','device_id':'device','origin':'desktop','session_id':'session','turn_id':'turn',
            'root_hash':'a'*64,'next_step':1,'nonce':str(uuid4()),'messages':[{'role':'system','content':'Readonly local tools'},{'role':'user','content':'Find fixture'}],
            'capabilities':['search_media','get_metadata'],'known_media_ids':[],'destination_grant_ids':['opaque-target'],
            'summary_count':0,'usage':{'prompt_tokens':1},'pending_calls':[{'callId':'call-one','tool':'search_media','arguments':{'query':'fixture'}}]}
        self.turn={'query':'fixture','sessionId':'session','turnId':'turn','useModel':True,'capabilities':['search_media']}
        self.media={'mediaId':'media-one','name':'fixture.jpg','kind':'photo','bytes':10,'qualityState':'good'}
    def issue(self,state=None,settings=None):return issue_local_state(*(settings or self.settings),self.state if state is None else state)
    def verify(self,token,principal=None,settings=None,**kwargs):return verify_local_state(token,*(settings or self.settings),principal or self.principal,**kwargs)
    def result(self,tool,data,status='succeeded',**changes):return LocalToolResult.model_validate({'callId':'call-one','tool':tool,'status':status,'data':data,**changes})

    def test_turn_explicit_consent_capabilities_and_opaque_grants(self):
        value=LocalTurnRequest.model_validate({**self.turn,'destinationGrantIds':['grant-one']})
        self.assertEqual(value.session_id,'session');self.assertEqual(value.destination_grant_ids,['grant-one'])
        for changes in ({'useModel':False},{'useModel':1},{'capabilities':['search_media','search_media']},{'capabilities':['shell']},
                        {'ownerId':'forged'},{'query':' '},{'sessionId':''},{'turnId':'x'*161},{'destinationGrantIds':['x'*501]},
                        {'destinationGrantIds':['same','same']},{'destinationGrantIds':['g'+str(i) for i in range(21)]}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):LocalTurnRequest.model_validate({**self.turn,**changes})

    def test_tool_arguments_strict_types_defaults_and_unknown_fields(self):
        self.assertEqual(validate_arguments('search_media',{'query':'fixture'}),{'query':'fixture','kind':'all','quality':'all','limit':10})
        self.assertEqual(validate_arguments('get_metadata',{'mediaId':'one'}),{'mediaId':'one'})
        self.assertEqual(validate_arguments('open_item',{'mediaId':'one'}),{'mediaId':'one'})
        self.assertEqual(validate_arguments('get_backup_job',{'jobId':'job'}),{'jobId':'job'})
        for tool,args in [('shell',{}),('rename_item',{'mediaId':'one'}),('search_media',{'query':'x','limit':True}),
                          ('search_media',{'query':'x','limit':11}),('search_media',{'query':'x','owner':'forged'}),
                          ('search_media',{'query':'x','quality':'invalid'}),('open_item',{'mediaId':'one','path':'private'}),
                          ('get_metadata',{'mediaId':'\n'}),('get_backup_job',{'jobId':'x'*161})]:
            with self.subTest(tool=tool,args=args),self.assertRaises(ValueError):validate_arguments(tool,args)

    def test_safe_result_shapes_and_sensitive_field_rejection(self):
        search={'matches':[self.media],'truncated':False}
        metadata={'media':{**self.media,'metadata':{'width':6000,'height':4000,'capturedAt':'2026-01-01T00:00:00Z'}}}
        job={'job':{'jobId':'job','name':'Fixture','state':'running','phase':'copy','files':1,'totalBytes':10,'progress':10,
                    'verify':True,'targets':[{'state':'running','copied':1,'skipped':0,'verified':1,'failed':0}]}}
        self.assertEqual(self.result('search_media',search).data,search)
        self.assertEqual(self.result('get_metadata',metadata).data,metadata)
        self.assertEqual(self.result('get_backup_job',job).data['job']['totalBytes'],10)
        self.assertTrue(self.result('open_item',{'opened':True,'mediaId':'media-one','receiptId':'receipt-1'}).data['opened'])
        for invalid_open in ({'opened':False,'mediaId':'media-one','receiptId':'receipt-1'},
                             {'opened':True,'mediaId':'media-one'},
                             {'opened':True,'mediaId':'media-one','receiptId':'receipt\n1'}):
            with self.subTest(open_result=invalid_open),self.assertRaises(ValidationError):self.result('open_item',invalid_open)
        for key in ('path','paths','GPS','EXIF','hash','fingerprint','owner','token'):
            with self.subTest(key=key),self.assertRaises(ValidationError):self.result('search_media',{'matches':[{**self.media,key:'private'}],'truncated':False})
        with self.assertRaises(ValidationError):self.result('get_metadata',{'media':{**self.media,'metadata':{'width':1,'height':1,'GPS':'private'}}})
        with self.assertRaises(ValidationError):self.result('search_media',{'matches':[self.media]*11,'truncated':True})
        with self.assertRaises(ValidationError):self.result('get_backup_job',{'job':{**job['job'],'progress':float('nan')}})
        with self.assertRaises(ValidationError):self.result('search_media',{'matches':[{**self.media,'bytes':True}],'truncated':False})
        self.assertEqual(self.result('search_media',{},status='failed').data,{})
        with self.assertRaises(ValidationError):self.result('search_media',search,status='declined')
        with self.assertRaises(ValidationError):self.result('search_media',{})

    def test_continuation_requires_unique_one_or_two_results_and_consent(self):
        result={'callId':'call-one','tool':'open_item','status':'declined','data':{}}
        self.assertEqual(ContinuationRequest.model_validate({'continuationToken':'signed','results':[result],'useModel':True}).results[0].status,'declined')
        for changes in ({'results':[]},{'results':[result,result]},{'useModel':1},{'owner_id':'forged'},{'continuationToken':'x'*131073}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):ContinuationRequest.model_validate({'continuationToken':'signed','results':[result],'useModel':True,**changes})

    def test_actual_rsa_state_is_principal_bound_and_key_not_in_payload(self):
        token=self.issue();clean=self.verify(token)
        self.assertEqual(clean['owner_id'],'owner');self.assertEqual(clean['pending_calls'][0]['arguments']['limit'],10)
        self.assertNotIn('iat',clean);self.assertNotIn('PRIVATE KEY',json.dumps(jwt.decode(token,options={'verify_signature':False})))
        claims=jwt.decode(token,jwt.PyJWK.from_dict(public_jwks(self.private,'local-key')['keys'][0]).key,algorithms=['RS256'],issuer=self.settings[2],audience='yingxu-app::local-tools')
        self.assertEqual(claims['exp']-claims['iat'],300);self.assertEqual(claims['jti'],self.state['nonce'])
        self.assertEqual(jwt.get_unverified_header(token)['typ'],STATE_TYP)
        self.assertEqual(self.state['pending_calls'][0]['arguments'],{'query':'fixture'})
        for principal in (SimpleNamespace(owner_id='other',device_id='device',origin='desktop'),SimpleNamespace(owner_id='owner',device_id='other',origin='desktop'),SimpleNamespace(owner_id='owner',device_id='device',origin='mobile')):
            with self.assertRaises(InvalidLocalState):self.verify(token,principal)

    def test_actual_expiration_and_session_token_cross_type_rejection(self):
        with patch('app.local_tool_protocol.time.time',return_value=int(time.time())-1000):token=self.issue()
        with self.assertRaises(LocalStateExpired):self.verify(token)
        session=issue_device_session(*self.settings,owner_id='owner',device_id='device',origin='desktop')
        with self.assertRaises(InvalidLocalState):self.verify(session)
        with self.assertRaises(InvalidDeviceSession):verify_device_session(self.issue(),*self.settings)

    def test_claim_header_signature_injection_and_algorithm_confusion_rejected(self):
        token=self.issue();claims=jwt.decode(token,options={'verify_signature':False})
        def signed(body,headers=None,key=None,algorithm='RS256'):
            return jwt.encode(body,key or self.private,algorithm=algorithm,headers=headers or {'kid':'local-key','typ':STATE_TYP})
        invalid=[signed(claims,key=self.other),signed(claims,key='synthetic-hmac-key',algorithm='HS256'),
                 signed({**claims,'owner':'forged'}),signed({**claims,'aud':'yingxu-app'}),signed({**claims,'exp':claims['exp']+1}),
                 signed({**claims,'jti':str(uuid4())}),signed(claims,{'kid':'local-key','typ':STATE_TYP,'jku':'https://attacker.invalid'}),
                 signed(claims,{'kid':'unknown','typ':STATE_TYP}),signed({**claims,'nbf':claims['iat']-1})]
        for value in invalid:
            with self.assertRaises(InvalidLocalState):self.verify(value)
        with self.assertRaises(InvalidLocalState):self.verify('x'*131073)

    def test_public_key_rotation_accepts_old_signature_only_while_listed(self):
        token=self.issue();next_settings=(self.other,'next-key',self.settings[2],self.settings[3])
        old=public_jwks(self.private,'local-key')
        self.assertEqual(self.verify(token,settings=next_settings,verification_jwks=old)['nonce'],self.state['nonce'])
        with self.assertRaises(InvalidLocalState):self.verify(token,settings=next_settings)
        with self.assertRaises(SessionTokenConfigurationError):self.verify(token,settings=(None,*self.settings[1:]))

    def test_state_fields_pending_calls_usage_and_size_are_strict(self):
        for changes in ({'owner':'forged'},{'private_key':'secret'},{'root_hash':'z'*64},{'next_step':0},{'next_step':True},
                        {'nonce':'not-uuid'},{'known_media_ids':['m']*11},{'usage':{'total_tokens':True}},
                        {'usage':{'total_tokens':-1}},{'usage':{'api_key':1}},{'pending_calls':[]},
                        {'pending_calls':[{'callId':'one','tool':'rename_item','arguments':{'mediaId':'x'}}]},
                        {'messages':[{'role':'user','content':'x'*65537}]},
                        {'messages':[{'role':'tool','tool_call_id':'call-one','content':'['*2000+']'*2000}]}):
            with self.subTest(changes=changes),self.assertRaises(InvalidLocalState):self.issue({**self.state,**changes})

    def test_signed_message_shapes_reject_credentials_and_allow_only_safe_tool_names(self):
        call={'id':'call-one','type':'function','function':{'name':'search_media','arguments':'{"query":"fixture"}'}}
        messages=self.state['messages']+[{'role':'assistant','content':None,'tool_calls':[call]},
                    {'role':'tool','tool_call_id':'call-one','content':json.dumps({'matches':[self.media],'truncated':False})}]
        self.assertEqual(self.verify(self.issue({**self.state,'messages':messages}))['messages'][-1]['role'],'tool')
        for message in ({'role':'user','content':'x','authorization':'secret'},
                        {'role':'assistant','content':None,'tool_calls':[{**call,'function':{'name':'shell','arguments':'{}'}}]},
                        {'role':'tool','tool_call_id':'call-one','content':'{"token":"secret"}'},
                        {'role':'tool','tool_call_id':'call-one','content':'{"sourceFingerprint":"private"}'},
                        {'role':'tool','tool_call_id':'call-one','content':'{"filePath":"private"}'},
                        {'role':'assistant','content':None,'tool_calls':[{**call,'function':{'name':'rename_item','arguments':'{"path":"private"}'}}]},
                        {'role':'assistant','content':None,'tool_calls':[{**call,'function':{'name':'search_media','arguments':'{"query":"x","query":"y"}'}}]}):
            with self.subTest(message=message),self.assertRaises(InvalidLocalState):self.issue({**self.state,'messages':[message]})
        write={'role':'assistant','content':None,'tool_calls':[{**call,'function':{'name':'rename_item','arguments':'{"mediaId":"media-one","newName":"new.jpg"}'}}]}
        with self.assertRaises(InvalidLocalState):self.issue({**self.state,'messages':[write]})

if __name__=='__main__':unittest.main()
