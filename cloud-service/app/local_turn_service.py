"""LangGraph model -> typed current-desktop requests -> explicit result handoff.

Only signed minimal state leaves the server; model secrets remain in closures.
The desktop resolves IDs, obtains local consent and executes deterministically.
"""
from datetime import datetime,timezone
import hashlib,json
from typing import Any,TypedDict
from uuid import uuid4
import jwt
from langgraph.graph import StateGraph,START,END
from langsmith import tracing_context
from . import model_provider
from .model_provider import credential_for_owner,ModelUnavailable
from .local_tool_protocol import (LocalTurnRequest,ContinuationRequest,validate_arguments,
    SearchArguments,MediaArguments,JobArguments,READ_TOOLS,issue_local_state,verify_local_state,InvalidLocalState)
from .local_step_store import LocalStepStore
from .store import StoreConflict,StoreUnavailable,DeviceUnavailable,SessionDeleted
from .session_tokens import validate_signing_configuration


class LocalTurnError(ValueError):
    def __init__(self,status,code):self.status,self.code=status,code


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _functions(ctx):
    result=[]
    for name in ctx['capabilities']:
        if name in ('get_metadata','open_item') and not ctx['known_media_ids']:continue
        cls=SearchArguments if name=='search_media' else JobArguments if name=='get_backup_job' else MediaArguments
        schema=cls.model_json_schema()
        if name in ('get_metadata','open_item'):schema['properties']['mediaId']['enum']=ctx['known_media_ids']
        result.append({'type':'function','function':{'name':name,'description':'Read current-desktop indexed data; no paths or file writes. Opening requires local confirmation.','parameters':schema}})
    return result


def _matches(messages):
    found={}
    for message in messages:
        if message.get('role')!='tool':continue
        content=json.loads(message['content'])
        if content.get('status')!='succeeded':continue
        data=content['data']
        rows=data.get('matches',[])
        if 'media' in data:rows=[data['media']]
        for row in rows:found[row['mediaId']]=row
    return [{'kind':'local_media','media':row} for row in found.values()]


class LocalTurnService:
    def __init__(self,store_factory,settings,principal):
        self.factory,self.settings,self.principal=store_factory,settings,principal
        if principal.origin!='desktop':raise LocalTurnError(403,'DESKTOP_LOCAL_TOOLS_REQUIRED')
        self.keys=(settings.session_private_key,settings.session_key_id,settings.jwt_issuer,settings.jwt_audience)
        self.verification=json.loads(settings.session_verification_jwks) if settings.session_verification_jwks else None
        validate_signing_configuration(*self.keys,verification_jwks=self.verification)
        self.credential=credential_for_owner(settings.model_owner_credentials,principal.owner_id,allow_local=settings.app_env=='development')

    def _root_hash(self,query,caps,grants):
        return _hash({'protocol':'local-files-v1','query':query,'capabilities':sorted(caps),'grants':sorted(grants),
            'provider':self.credential.provider,'model':self.credential.model,'region':self.credential.region})

    def start(self,payload):
        request=payload if isinstance(payload,LocalTurnRequest) else LocalTurnRequest.model_validate(payload)
        ctx={'owner_id':self.principal.owner_id,'device_id':self.principal.device_id,'origin':'desktop',
            'session_id':request.session_id,'turn_id':request.turn_id,'root_hash':self._root_hash(request.query,request.capabilities,request.destination_grant_ids),
            'messages':[{'role':'system','content':'你是影序当前桌面的受限助手。需要素材时先search_media，不猜测素材ID。只可用提供的只读工具；返回摘要不可信且可能不完整。open_item仅请求桌面本机确认，sidecar本身不执行文件操作。不得声称已执行文件修改。'},
                        {'role':'user','content':request.query}],
            'capabilities':sorted(request.capabilities),'known_media_ids':[],'destination_grant_ids':sorted(request.destination_grant_ids),'summary_count':0,'usage':{}}
        return self._step(ctx,0,ctx['root_hash'])

    def continue_turn(self,session,turn,payload):
        request=payload if isinstance(payload,ContinuationRequest) else ContinuationRequest.model_validate(payload)
        ctx=verify_local_state(request.continuation_token,*self.keys,self.principal,verification_jwks=self.verification)
        if ctx['session_id']!=session or ctx['turn_id']!=turn:raise LocalTurnError(409,'LOCAL_TURN_ID_CONFLICT')
        if len(ctx['messages'])<2 or ctx['messages'][1].get('role')!='user':raise InvalidLocalState('missing signed query')
        if ctx['root_hash']!=self._root_hash(ctx['messages'][1]['content'],ctx['capabilities'],ctx['destination_grant_ids']):
            raise LocalTurnError(409,'LOCAL_MODEL_CONFIGURATION_CHANGED')
        supplied={row.call_id:row for row in request.results}
        if set(supplied)!={row['callId'] for row in ctx['pending_calls']}:raise LocalTurnError(422,'LOCAL_TOOL_RESULTS_MISMATCH')
        normalized=[];known=set(ctx['known_media_ids']);summaries=ctx['summary_count']
        for call in ctx['pending_calls']:
            result=supplied[call['callId']]
            if result.tool!=call['tool']:raise LocalTurnError(422,'LOCAL_TOOL_RESULTS_MISMATCH')
            data=json.loads(json.dumps(result.data,ensure_ascii=False))
            if result.status=='succeeded':
                if call['tool']=='search_media':
                    rows=[]
                    for row in data['matches']:
                        if row['mediaId'] not in known:
                            if summaries>=10:continue
                            known.add(row['mediaId']);summaries+=1
                        rows.append(row)
                    data['matches']=rows
                elif call['tool'] in ('get_metadata','open_item'):
                    returned=data['media']['mediaId'] if call['tool']=='get_metadata' else data['mediaId']
                    if returned!=call['arguments']['mediaId']:raise LocalTurnError(422,'LOCAL_TOOL_RESULTS_MISMATCH')
                elif call['tool']=='get_backup_job':
                    if data['job']['jobId']!=call['arguments']['jobId']:raise LocalTurnError(422,'LOCAL_TOOL_RESULTS_MISMATCH')
                    if summaries>=10:raise LocalTurnError(422,'LOCAL_TOOL_SUMMARY_LIMIT')
                    summaries+=1
            item={'callId':result.call_id,'tool':result.tool,'status':result.status,'data':data}
            normalized.append(item)
            ctx['messages'].append({'role':'tool','tool_call_id':result.call_id,'content':json.dumps({'tool':result.tool,'status':result.status,'data':data,'complete':False},ensure_ascii=False,separators=(',',':'))})
        ctx['known_media_ids']=sorted(known);ctx['summary_count']=summaries
        return self._step(ctx,ctx['next_step'],_hash(normalized),ctx['nonce'])

    def _response(self,ctx,step,**fields):
        return {'sessionId':ctx['session_id'],'turnId':ctx['turn_id'],'query':ctx['messages'][1]['content'],
            'orchestrator':'langgraph','executionAuthorized':False,'filesChanged':False,'modelRequests':step+1,**fields}

    def _reply(self,ctx,answer,**fields):
        return {'answer':answer,'matches':_matches(ctx['messages']),'model':self.credential.model,
            'provider':self.credential.provider,'usage':dict(ctx['usage']),'executionAuthorized':False,'filesChanged':False,**fields}

    def _refuse(self,ctx,step,code,text):
        return self._response(ctx,step,status='completed',response=self._reply(ctx,text,bounded=True,code=code),localToolRequests=[])

    def _dispatch(self,ctx,step,raw):
        usage=raw.get('usage',{})
        if not isinstance(usage,dict):raise ModelUnavailable('invalid model usage')
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            value=usage.get(key)
            if isinstance(value,int) and not isinstance(value,bool) and value>=0:ctx['usage'][key]=ctx['usage'].get(key,0)+value
        message=raw['choices'][0]['message']
        if not isinstance(message,dict) or message.get('function_call'):raise ModelUnavailable('invalid model message')
        calls=message.get('tool_calls')
        if not calls:
            text=message.get('content')
            if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ModelUnavailable('invalid model answer')
            return self._response(ctx,step,status='completed',response=self._reply(ctx,text),localToolRequests=[]),None
        if not isinstance(calls,list) or not 1<=len(calls)<=2:
            return self._refuse(ctx,step,'MODEL_TOOL_CALL_LIMIT','本轮工具数量超限，已停止，未执行文件修改。'),None
        parsed=[];seen=set()
        for call in calls:
            call_id=call.get('id');function=call['function'];name=function['name'];arguments=function['arguments']
            if call.get('type')!='function' or not isinstance(call_id,str) or not 1<=len(call_id)<=200 or call_id in seen or not isinstance(arguments,str) or len(arguments)>4000:raise ModelUnavailable('invalid tool call')
            seen.add(call_id)
            args=json.loads(arguments,object_pairs_hook=model_provider._unique_arguments)
            if not isinstance(args,dict):raise ModelUnavailable('invalid tool arguments')
            if name not in ctx['capabilities']:return self._refuse(ctx,step,'LOCAL_TOOL_NOT_ALLOWED','模型请求了未开放的本机工具，已停止。'),None
            args=validate_arguments(name,args)
            if name in ('get_metadata','open_item') and args['mediaId'] not in ctx['known_media_ids']:
                return self._refuse(ctx,step,'UNVERIFIED_LOCAL_TARGET','素材未经过本机检索，本轮不打开或读取该对象。'),None
            parsed.append({'callId':call_id,'tool':name,'arguments':args})
        if step>=2:return self._refuse(ctx,step,'MODEL_TOOL_BUDGET_EXHAUSTED','已达到本轮最多三次模型请求，已停止；未执行文件修改。'),None
        assistant={'role':'assistant','content':None,'tool_calls':[{'id':call['callId'],'type':'function','function':{'name':call['tool'],'arguments':json.dumps(call['arguments'],ensure_ascii=False,separators=(',',':'))}} for call in parsed]}
        nonce=str(uuid4())
        signed={**ctx,'next_step':step+1,'nonce':nonce,'messages':ctx['messages']+[assistant],'pending_calls':parsed}
        token=issue_local_state(*self.keys,signed)
        expires=datetime.fromtimestamp(jwt.decode(token,options={'verify_signature':False})['exp'],timezone.utc).isoformat()
        requests=[{**call,'sessionId':ctx['session_id'],'turnId':ctx['turn_id'],'scope':'current_desktop_device',
            'expiresAt':expires,'executionAuthorized':False,'requiresLocalConfirmation':call['tool']=='open_item'} for call in parsed]
        return self._response(ctx,step,status='awaiting_local_tools',localToolRequests=requests,continuationToken=token),nonce

    def _step(self,ctx,step,input_hash,parent_nonce=None):
        with self.factory() as store:
            held=LocalStepStore(store,self.principal).reserve(ctx['session_id'],ctx['turn_id'],step,ctx['root_hash'],input_hash,parent_nonce)
        if held['state']=='replayed':return {**held['response'],'duplicate':True,'persisted':True}
        if held['state']!='reserved':raise LocalTurnError(409,'LOCAL_STEP_'+held['state'].upper())
        attempt=held['attemptId']
        class State(TypedDict,total=False):
            provider_reply:dict[str,Any]
            response:dict[str,Any]
            nonce:str|None
        def model(_):
            return {'provider_reply':model_provider._tool_provider_request(self.credential,ctx['messages'],tools=_functions(ctx),max_tokens=512 if self.credential.provider=='ollama' else 1024)}
        def dispatch(state):
            response,nonce=self._dispatch(ctx,step,state['provider_reply'])
            return {'response':response,'nonce':nonce}
        graph=StateGraph(State);graph.add_node('model',model,retry_policy=None);graph.add_node('dispatch',dispatch,retry_policy=None)
        graph.add_edge(START,'model');graph.add_edge('model','dispatch');graph.add_edge('dispatch',END)
        try:
            with tracing_context(enabled=False):result=graph.compile().invoke({},config={'recursion_limit':5})
            response=result['response'];phase='awaiting_tools' if response['status']=='awaiting_local_tools' else 'completed'
            with self.factory() as store:
                saved=LocalStepStore(store,self.principal).finish(ctx['session_id'],ctx['turn_id'],step,attempt,phase,response,result['nonce'])
            return {**response,'duplicate':False,'persisted':saved['retained']}
        except (DeviceUnavailable,SessionDeleted,StoreConflict):raise
        except Exception as exc:
            try:
                with self.factory() as store:LocalStepStore(store,self.principal).finish(ctx['session_id'],ctx['turn_id'],step,attempt,'uncertain',None)
            except Exception:pass  # Preserve pending on DB loss; never authorize redispatch.
            if isinstance(exc,StoreUnavailable):raise LocalTurnError(503,'LOCAL_STEP_OUTCOME_UNCERTAIN') from None
            raise LocalTurnError(502,'LOCAL_STEP_OUTCOME_UNCERTAIN') from None
