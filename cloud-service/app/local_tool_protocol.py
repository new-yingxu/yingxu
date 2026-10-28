"""Strict local-tool handoff DTOs and short-lived signed continuation state.

State is authenticated, not encrypted. Never include file paths, raw metadata,
credentials or a signing key; only the approved projections belong here.
No device-session token is accepted as local-tool continuation state.
"""
from typing import Any,Literal
from uuid import UUID
import json,re,time
import jwt
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from .session_tokens import public_jwks,validate_signing_configuration,SessionTokenConfigurationError

READ_TOOLS=('search_media','get_metadata','get_backup_job','open_item')
ToolName=Literal['search_media','get_metadata','get_backup_job','open_item']
STATE_TYP='yingxu-local-tool-state+jwt'
STATE_LIFETIME=300
MAX_MESSAGES_BYTES=65536
MAX_STATE_TOKEN=131072


class InvalidLocalState(ValueError):pass
class LocalStateExpired(InvalidLocalState):pass


class StrictDTO(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class LocalTurnRequest(StrictDTO):
    query:str=Field(min_length=1,max_length=500)
    session_id:str=Field(alias='sessionId',min_length=1,max_length=160)
    turn_id:str=Field(alias='turnId',min_length=1,max_length=160)
    use_model:Literal[True]=Field(alias='useModel')
    capabilities:list[ToolName]=Field(max_length=4)
    destination_grant_ids:list[str]=Field(alias='destinationGrantIds',default_factory=list,max_length=20)

    @field_validator('use_model',mode='before')
    @classmethod
    def explicit_model(cls,value):
        if value is not True:raise ValueError('useModel requires explicit true')
        return value

    @field_validator('query','session_id','turn_id')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('nonempty value required')
        return value

    @field_validator('capabilities')
    @classmethod
    def unique_capabilities(cls,value):
        if len(value)!=len(set(value)):raise ValueError('duplicate capability')
        return value

    @field_validator('destination_grant_ids')
    @classmethod
    def grants(cls,value):
        return _ids(value,500,20)


class SearchArguments(StrictDTO):
    query:str=Field(min_length=1,max_length=500)
    kind:Literal['all','photo','video','other']='all'
    quality:Literal['all','good','review','unanalyzed']='all'
    limit:int=Field(default=10,ge=1,le=10)
    @field_validator('query')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('empty query')
        return value


class MediaArguments(StrictDTO):
    mediaId:str=Field(min_length=1,max_length=160)


class JobArguments(StrictDTO):
    jobId:str=Field(min_length=1,max_length=160)


def validate_arguments(tool,args):
    models={'search_media':SearchArguments,'get_metadata':MediaArguments,'open_item':MediaArguments,'get_backup_job':JobArguments}
    if not isinstance(tool,str) or tool not in models:raise ValueError('local read tool not allowed')
    result=models[tool].model_validate(args).model_dump()
    for key in ('mediaId','jobId'):
        if key in result:_id(result[key],160)
    return result


class MediaSummary(StrictDTO):
    @field_validator('mediaId',check_fields=False)
    @classmethod
    def media_id(cls,value):return _id(value,160)
    mediaId:str=Field(min_length=1,max_length=160)
    name:str=Field(min_length=1,max_length=500)
    kind:Literal['photo','video','other']
    bytes:int=Field(ge=0)
    qualityState:str=Field(min_length=1,max_length=100)


class SearchData(StrictDTO):
    matches:list[MediaSummary]=Field(max_length=10)
    truncated:bool


class SafeMetadata(StrictDTO):
    width:int|None=Field(default=None,ge=0)
    height:int|None=Field(default=None,ge=0)
    capturedAt:str|None=Field(default=None,max_length=100)


class MetadataMedia(MediaSummary):
    metadata:SafeMetadata


class MetadataData(StrictDTO):
    media:MetadataMedia


class JobTarget(StrictDTO):
    state:str=Field(min_length=1,max_length=100)
    copied:int=Field(ge=0)
    skipped:int=Field(ge=0)
    verified:int=Field(ge=0)
    failed:int=Field(ge=0)


class JobSummary(StrictDTO):
    @field_validator('jobId',check_fields=False)
    @classmethod
    def job_id(cls,value):return _id(value,160)
    jobId:str=Field(min_length=1,max_length=160)
    name:str=Field(min_length=1,max_length=500)
    state:str=Field(min_length=1,max_length=100)
    phase:str=Field(min_length=1,max_length=100)
    files:int=Field(ge=0)
    totalBytes:int=Field(ge=0)
    progress:float=Field(ge=0,le=100)
    verify:bool
    targets:list[JobTarget]=Field(max_length=10)


class JobData(StrictDTO):
    job:JobSummary


class OpenData(StrictDTO):
    @field_validator('mediaId',check_fields=False)
    @classmethod
    def media_id(cls,value):return _id(value,160)
    opened:Literal[True]
    mediaId:str=Field(min_length=1,max_length=160)
    receiptId:str=Field(min_length=1,max_length=200)

    @field_validator('receiptId')
    @classmethod
    def receipt_id(cls,value):return _id(value,200)


_RESULT_MODELS={'search_media':SearchData,'get_metadata':MetadataData,'get_backup_job':JobData,'open_item':OpenData}


class LocalToolResult(StrictDTO):
    @field_validator('call_id',check_fields=False)
    @classmethod
    def call_id_value(cls,value):return _id(value,200)
    call_id:str=Field(alias='callId',min_length=1,max_length=200)
    tool:ToolName
    status:Literal['succeeded','failed','declined']
    data:dict[str,Any]

    @model_validator(mode='after')
    def safe_projection(self):
        if self.status!='succeeded':
            if self.data:raise ValueError('failed or declined tool data must be empty')
        else:self.data=_RESULT_MODELS[self.tool].model_validate(self.data).model_dump(exclude_none=True)
        return self


class ContinuationRequest(StrictDTO):
    continuation_token:str=Field(alias='continuationToken',min_length=1,max_length=MAX_STATE_TOKEN)
    results:list[LocalToolResult]=Field(min_length=1,max_length=2)
    use_model:Literal[True]=Field(alias='useModel')
    @field_validator('use_model',mode='before')
    @classmethod
    def explicit_model(cls,value):
        if value is not True:raise ValueError('useModel requires explicit true')
        return value
    @field_validator('results')
    @classmethod
    def unique_calls(cls,value):
        if len({item.call_id for item in value})!=len(value):raise ValueError('duplicate call result')
        return value


def _id(value,maximum):
    if not isinstance(value,str) or not 1<=len(value)<=maximum or not value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('invalid opaque id')
    return value


def _ids(values,maximum,count):
    if not isinstance(values,list) or len(values)>count:raise ValueError('invalid ids')
    result=[_id(value,maximum) for value in values]
    if len(result)!=len(set(result)):raise ValueError('duplicate id')
    return result


def _nonce(value):
    try:
        if not isinstance(value,str):raise ValueError()
        parsed=UUID(value)
        if parsed.version!=4 or str(parsed)!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('invalid continuation nonce') from None
    return value


def _invalid_constant(value):
    raise ValueError('non-JSON numeric constant')


def _unique_json(pairs):
    output={}
    for key,value in pairs:
        if key in output:raise ValueError('duplicate JSON key')
        output[key]=value
    return output


def _reject_sensitive(value):
    forbidden={'path','paths','sourcepath','destinationpath','rawexif','exif','gps','latitude','longitude','hash','sha256','fingerprint',
               'owner','ownerid','deviceid','authorization','token','accesstoken','refreshtoken','apikey','password','secret','credentials','privatekey'}
    if isinstance(value,dict):
        for key,child in value.items():
            normalized=re.sub(r'[-_]','',str(key)).lower()
            if (normalized in forbidden or normalized.startswith(('owner','rawexif','gps'))
                or normalized.endswith(('path','paths','fingerprint','token','secret','password','apikey','credentials'))):
                raise ValueError('sensitive field in local tool message')
            _reject_sensitive(child)
    elif isinstance(value,list):
        for child in value:_reject_sensitive(child)


def _calls(values):
    if not isinstance(values,list) or not 1<=len(values)<=2:raise ValueError('invalid tool calls')
    ids=set();output=[]
    for call in values:
        if not isinstance(call,dict) or set(call)!={'id','type','function'} or call['type']!='function':raise ValueError('invalid tool call')
        call_id=_id(call['id'],200)
        if call_id in ids:raise ValueError('duplicate tool call id')
        ids.add(call_id)
        function=call['function']
        if not isinstance(function,dict) or set(function)!={'name','arguments'}:raise ValueError('invalid function')
        name=function['name'];arguments=function['arguments']
        if not isinstance(name,str) or name not in READ_TOOLS or not isinstance(arguments,str) or len(arguments)>4000:raise ValueError('tool not allowed')
        parsed=json.loads(arguments,object_pairs_hook=_unique_json,parse_constant=_invalid_constant)
        if not isinstance(parsed,dict):raise ValueError('object arguments required')
        _reject_sensitive(parsed)
        if name in READ_TOOLS:parsed=validate_arguments(name,parsed)
        output.append({'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(parsed,ensure_ascii=False,separators=(',',':'))}})
    return output


def _messages(messages):
    if not isinstance(messages,list) or len(messages)>64:raise ValueError('invalid messages')
    output=[]
    for message in messages:
        if not isinstance(message,dict):raise ValueError('invalid message')
        role=message.get('role')
        if role in ('system','user'):
            if set(message)!={'role','content'} or not isinstance(message['content'],str):raise ValueError('invalid text message')
            output.append(dict(message))
        elif role=='assistant':
            if set(message) not in ({'role','content'},{'role','content','tool_calls'}) or (message['content'] is not None and not isinstance(message['content'],str)):
                raise ValueError('invalid assistant message')
            clean={'role':'assistant','content':message['content']}
            if 'tool_calls' in message:clean['tool_calls']=_calls(message['tool_calls'])
            elif message['content'] is None:raise ValueError('empty assistant message')
            output.append(clean)
        elif role=='tool':
            if set(message)!={'role','tool_call_id','content'} or not isinstance(message['content'],str):raise ValueError('invalid tool message')
            _id(message['tool_call_id'],200)
            try:content=json.loads(message['content'],object_pairs_hook=_unique_json,parse_constant=_invalid_constant)
            except json.JSONDecodeError:content=None
            if content is not None:_reject_sensitive(content)
            output.append(dict(message))
        else:raise ValueError('message role not allowed')
    if len(json.dumps(output,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())>MAX_MESSAGES_BYTES:
        raise ValueError('message state exceeds size limit')
    return output


class PendingCall(StrictDTO):
    @field_validator('callId',check_fields=False)
    @classmethod
    def call_id_value(cls,value):return _id(value,200)
    callId:str=Field(min_length=1,max_length=200)
    tool:ToolName
    arguments:dict[str,Any]
    @model_validator(mode='after')
    def safe_arguments(self):
        self.arguments=validate_arguments(self.tool,self.arguments)
        return self


class SignedLocalState(StrictDTO):
    owner_id:str=Field(min_length=1,max_length=160)
    device_id:str=Field(min_length=1,max_length=500)
    origin:Literal['desktop']
    session_id:str=Field(min_length=1,max_length=160)
    turn_id:str=Field(min_length=1,max_length=160)
    root_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    next_step:int=Field(ge=1,le=2)
    nonce:str
    messages:list[dict[str,Any]]
    capabilities:list[ToolName]=Field(max_length=4)
    known_media_ids:list[str]=Field(max_length=10)
    destination_grant_ids:list[str]=Field(max_length=20)
    summary_count:int=Field(ge=0,le=10)
    usage:dict[str,int]
    pending_calls:list[PendingCall]=Field(min_length=1,max_length=2)

    @field_validator('owner_id','device_id','session_id','turn_id')
    @classmethod
    def labels(cls,value):return _id(value,500)
    @field_validator('nonce')
    @classmethod
    def nonce_value(cls,value):return _nonce(value)
    @field_validator('messages')
    @classmethod
    def messages_value(cls,value):return _messages(value)
    @field_validator('capabilities')
    @classmethod
    def capabilities_value(cls,value):
        if len(value)!=len(set(value)):raise ValueError('duplicate capability')
        return value
    @field_validator('known_media_ids')
    @classmethod
    def media_ids(cls,value):return _ids(value,160,10)
    @field_validator('destination_grant_ids')
    @classmethod
    def grant_ids(cls,value):return _ids(value,500,20)
    @field_validator('usage')
    @classmethod
    def token_usage(cls,value):
        if set(value)-{'prompt_tokens','completion_tokens','total_tokens'} or any(isinstance(v,bool) or v<0 for v in value.values()):
            raise ValueError('invalid reported token usage')
        return value
    @field_validator('pending_calls')
    @classmethod
    def unique_pending(cls,value):
        if len({item.callId for item in value})!=len(value):raise ValueError('duplicate pending call')
        return value


def issue_local_state(private,kid,issuer,audience,state):
    validate_signing_configuration(private,kid,issuer,audience)
    try:
        clean=SignedLocalState.model_validate(state).model_dump()
        now=int(time.time())
        claims={'iss':issuer,'aud':audience+'::local-tools','iat':now,'nbf':now,'exp':now+STATE_LIFETIME,'jti':clean['nonce'],'state':clean}
        token=jwt.encode(claims,private,algorithm='RS256',headers={'kid':kid,'typ':STATE_TYP})
        if len(token)>MAX_STATE_TOKEN:raise ValueError('state exceeds token size')
        return token
    except (ValueError,TypeError,UnicodeError,RecursionError) as exc:
        if isinstance(exc,SessionTokenConfigurationError):raise
        raise InvalidLocalState('invalid local tool state') from None


def verify_local_state(token,private,kid,issuer,audience,principal,verification_jwks=None):
    validate_signing_configuration(private,kid,issuer,audience,verification_jwks=verification_jwks)
    keys=public_jwks(private,kid,verification_jwks=verification_jwks)['keys']
    try:
        if not isinstance(token,str) or not 1<=len(token)<=MAX_STATE_TOKEN:raise ValueError()
        header=jwt.get_unverified_header(token)
        if set(header)!={'alg','kid','typ'} or header['alg']!='RS256' or header['typ']!=STATE_TYP:raise ValueError()
        selected=next((key for key in keys if key['kid']==header['kid']),None)
        if selected is None:raise ValueError()
        required={'iss','aud','iat','nbf','exp','jti','state'}
        claims=jwt.decode(token,jwt.PyJWK.from_dict(selected).key,algorithms=['RS256'],issuer=issuer,audience=audience+'::local-tools',
                          options={'require':list(required),'strict_aud':True})
        if set(claims)!=required or any(not isinstance(claims[k],int) or isinstance(claims[k],bool) for k in ('iat','nbf','exp')):raise ValueError()
        if claims['nbf']!=claims['iat'] or claims['exp']-claims['iat']!=STATE_LIFETIME:raise ValueError()
        clean=SignedLocalState.model_validate(claims['state']).model_dump()
        if claims['jti']!=clean['nonce'] or clean['origin']!='desktop' or principal.origin!='desktop':raise ValueError()
        if clean['owner_id']!=principal.owner_id or clean['device_id']!=principal.device_id:raise ValueError()
        return clean
    except jwt.ExpiredSignatureError:
        raise LocalStateExpired('local tool state expired') from None
    except (jwt.PyJWTError,ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError):
        raise InvalidLocalState('invalid local tool state') from None
