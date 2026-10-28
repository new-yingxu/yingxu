"""Real compiled LangGraph with mocked fixed-endpoint HTTP transport."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.model_provider import credential_for_owner,generate_answer,generate_tool_answer,ModelUnavailable,MAX_RESPONSE
from langsmith.utils import tracing_is_enabled


def tool(name='search_backup_jobs',arguments=None,call_id='call-1'):
    return {'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(arguments or {'query':'备份'})}}


def response(content=None,calls=None,usage=None):
    message={'role':'assistant','content':content}
    if calls is not None:message['tool_calls']=calls
    return {'choices':[{'message':message}],'usage':usage or {'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}}


class ModelTools(unittest.TestCase):
    def setUp(self):
        self.credential=credential_for_owner(json.dumps({'owner':{'apiKey':'synthetic-secret-do-not-store'}}),'owner')
        self.requests=[];self.queue=[]
        self.opener=MagicMock()
        def open_request(req,timeout):
            self.assertFalse(tracing_is_enabled())
            self.requests.append(json.loads(req.data))
            self.assertEqual(req.full_url,'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
            self.assertEqual(timeout,15)
            self.assertNotIn('synthetic-secret-do-not-store',req.data.decode())
            value=self.queue.pop(0)
            if isinstance(value,Exception):raise value
            transport=MagicMock();transport.__enter__.return_value.read.return_value=json.dumps(value).encode()
            return transport
        self.opener.open.side_effect=open_request
        self.transport=patch('app.model_provider.build_opener',return_value=self.opener)
        self.transport.start();self.addCleanup(self.transport.stop)

    def test_actual_graph_tool_cycle_messages_usage_and_context_minimization(self):
        self.queue=[response(calls=[tool(),tool('get_backup_job',{'jobId':'job'},'call-2')]),response('完成',usage={'prompt_tokens':5,'completion_tokens':2,'total_tokens':7})]
        invocations=[]
        def invoke(name,args):
            self.assertFalse(tracing_is_enabled());invocations.append((name,args))
            rows=[{'jobId':'job','name':'测试','state':'success','occurredAt':'now','paths':['/private/path'],'gps':'private-gps','source':'private-source','deviceId':'private-device'}]
            return {'matches' if name=='search_backup_jobs' else 'jobs':rows,'raw_secret':'forbidden-result'}
        answer=generate_answer(self.credential,'备份',[],invoke_tool=invoke)
        self.assertEqual(len(invocations),2)
        self.assertEqual(answer['answer'],'完成');self.assertFalse(answer['executionAuthorized'])
        self.assertEqual(answer['usage'],{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10})
        messages=self.requests[1]['messages']
        self.assertEqual([m.get('tool_call_id') for m in messages if m['role']=='tool'],['call-1','call-2'])
        raw=json.dumps(self.requests,ensure_ascii=False)
        for forbidden in ('/private/path','private-gps','private-source','private-device','forbidden-result','paths','gps','raw_secret'):
            self.assertNotIn(forbidden,raw)
        self.assertEqual({t['function']['name'] for t in self.requests[0]['tools']},{'search_backup_jobs','get_backup_job'})
        self.assertTrue(all(not t['function']['parameters']['additionalProperties'] for t in self.requests[0]['tools']))

    def test_round_budget_is_deterministic_without_automatic_retry(self):
        self.queue=[response(calls=[tool(call_id=f'call-{i}')]) for i in range(3)]
        invoke=MagicMock(return_value={'matches':[]})
        result=generate_tool_answer(self.credential,'备份',invoke)
        self.assertTrue(result['bounded']);self.assertEqual(result['code'],'MODEL_TOOL_BUDGET_EXHAUSTED')
        self.assertEqual(self.opener.open.call_count,3);self.assertEqual(invoke.call_count,2)
        self.assertEqual(result['usage']['total_tokens'],9)
        for body in self.requests[1:]:
            ids={c['id'] for m in body['messages'] if m['role']=='assistant' for c in m['tool_calls']}
            results={m['tool_call_id'] for m in body['messages'] if m['role']=='tool'}
            self.assertEqual(ids,results)

    def test_invalid_write_unknown_and_owner_injection_never_invoke_tool(self):
        for call in [tool('rename_item'),tool('unknown'),tool(arguments={'query':'备份','owner_id':'victim'}),
                     tool('get_backup_job',{'jobId':'job','ownerId':'victim'}),
                     tool(arguments={'query':' '})]:
            with self.subTest(call=call):
                self.queue=[response(calls=[call])];invoke=MagicMock()
                with self.assertRaises(ModelUnavailable):generate_tool_answer(self.credential,'备份',invoke)
                invoke.assert_not_called()
        duplicate=tool();duplicate['function']['arguments']='{"query":"a","query":"b"}'
        self.queue=[response(calls=[duplicate])]
        with self.assertRaises(ModelUnavailable):generate_tool_answer(self.credential,'备份',MagicMock())

    def test_cumulative_summary_and_per_round_call_limits(self):
        self.queue=[response(calls=[tool()]),response(calls=[tool(call_id='more')])]
        invoke=MagicMock(return_value={'matches':[{'jobId':str(i),'name':{'paths':'nested-private'}} for i in range(15)]})
        result=generate_tool_answer(self.credential,'备份',invoke)
        self.assertTrue(result['bounded']);self.assertEqual(invoke.call_count,1)
        tool_message=self.requests[1]['messages'][-1]
        summaries=json.loads(tool_message['content'])['syncedJobSummaries']
        self.assertEqual(len(summaries),10);self.assertTrue(all(set(row)=={'jobId'} for row in summaries))
        self.queue=[response(calls=[tool(call_id=str(i)) for i in range(3)])];invoke=MagicMock()
        result=generate_tool_answer(self.credential,'备份',invoke)
        self.assertEqual(result['code'],'MODEL_TOOL_CALL_LIMIT');invoke.assert_not_called()
        self.queue=[response(calls=[tool(call_id='first')]),response(calls=[tool(call_id='second')]),response('完成')]
        invoke=MagicMock(return_value={'jobs':[{'jobId':str(i)} for i in range(6)]})
        generate_tool_answer(self.credential,'备份',invoke)
        final_messages=self.requests[-1]['messages']
        counts=[len(json.loads(m['content'])['syncedJobSummaries']) for m in final_messages if m['role']=='tool']
        self.assertEqual(counts,[6,4]);self.assertEqual(invoke.call_count,2)

    def test_credentials_stay_out_of_actual_graph_state_and_failures_do_not_retry(self):
        from langgraph.graph import StateGraph
        original=StateGraph.compile;captured=[]
        def compile_spy(graph,*args,**kwargs):
            compiled=original(graph,*args,**kwargs)
            real_invoke=compiled.invoke
            def invoke_spy(inputs,*args,**kwargs):
                captured.append(inputs)
                result=real_invoke(inputs,*args,**kwargs);captured.append(result);return result
            compiled.invoke=invoke_spy
            return compiled
        self.queue=[response('只读回答')]
        with patch.object(StateGraph,'compile',compile_spy):
            generate_tool_answer(self.credential,'备份',MagicMock())
        raw=json.dumps(captured,ensure_ascii=False)
        self.assertNotIn('synthetic-secret-do-not-store',raw);self.assertNotIn('credential',raw)
        before=self.opener.open.call_count
        self.queue=[TimeoutError('fixture')]
        with self.assertRaises(ModelUnavailable):generate_tool_answer(self.credential,'备份',MagicMock())
        self.assertEqual(self.opener.open.call_count,before+1)
        self.queue=[response(calls=[tool()])]
        with self.assertRaises(ModelUnavailable):generate_tool_answer(self.credential,'备份',MagicMock(side_effect=RuntimeError('tool failed')))

    def test_local_fixed_transport_and_oversized_response(self):
        local=credential_for_owner(json.dumps({'owner':{'provider':'ollama','model':'qwen2.5:7b'}}),'owner',allow_local=True)
        transport=MagicMock();transport.open.return_value.__enter__.return_value.read.return_value=json.dumps(response('本地回答')).encode()
        with patch('app.model_provider.build_opener',return_value=transport) as factory:
            generate_tool_answer(local,'备份',MagicMock())
        self.assertEqual(factory.call_args.args[0].proxies,{})
        request=transport.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:11434/v1/chat/completions');self.assertNotIn('Authorization',request.headers)
        transport.open.return_value.__enter__.return_value.read.return_value=b'x'*(MAX_RESPONSE+1)
        with patch('app.model_provider.build_opener',return_value=transport),self.assertRaises(ModelUnavailable):
            generate_tool_answer(self.credential,'备份',MagicMock())


if __name__=='__main__':unittest.main()
