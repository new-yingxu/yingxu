"""Desktop-only, three-step durable reservations for explicit local tool handoff.

A pending or uncertain result never authorizes another provider call. Only
hashes/nonces/state survive opt-out; credentials must remain in caller closures.
"""
from copy import deepcopy
from datetime import datetime
import json
import re
from uuid import UUID,uuid4
from sqlalchemy import CheckConstraint,DateTime,Integer,JSON,String,select
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .models import Device,DeletedAgentSession,SyncPreferences,utcnow
from .store import SQLStore,StoreConflict,DeviceUnavailable,SessionDeleted

MAX_RESPONSE_BYTES=2*1024*1024
LOCAL_STEP_SECURITY_METADATA={'kind':'local_step_deduplication','reason':'Keep local-step session/turn/device IDs, root/input hashes, attempt IDs, nonces, states and timestamps; clear cached responses and never automatically repeat pending or uncertain model calls.'}
_FORBIDDEN={'apikey','api_key','authorization','password','credentials','secret','token','access_token','refresh_token','id_token','service_role_key','accesstoken','refreshtoken','idtoken','servicerolekey','client_secret','clientsecret'}


class LocalStep(Base):
    __tablename__='local_agent_steps'
    __table_args__=(CheckConstraint('step_index>=0 AND step_index<=2',name='ck_local_step_index'),
                   CheckConstraint("origin='desktop'",name='ck_local_step_desktop'),
                   CheckConstraint("state IN ('pending','awaiting_tools','completed','uncertain')",name='ck_local_step_state'))
    owner_id:Mapped[str]=mapped_column(String(160),primary_key=True)
    session_id:Mapped[str]=mapped_column(String(160),primary_key=True)
    turn_id:Mapped[str]=mapped_column(String(160),primary_key=True)
    step_index:Mapped[int]=mapped_column(Integer,primary_key=True)
    device_id:Mapped[str]=mapped_column(String(500))
    origin:Mapped[str]=mapped_column(String(16))
    root_hash:Mapped[str]=mapped_column(String(64))
    input_hash:Mapped[str]=mapped_column(String(64))
    attempt_id:Mapped[str]=mapped_column(String(36))
    output_nonce:Mapped[str|None]=mapped_column(String(36),nullable=True)
    state:Mapped[str]=mapped_column(String(30),default='pending')
    response_json:Mapped[dict|None]=mapped_column(JSON(none_as_null=True),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 or ord(c)==127 for c in value)


def _uuid(value):
    try:return isinstance(value,str) and len(value)==36 and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def _response(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('local step response must be a JSON object')
    def safe(item):
        if isinstance(item,dict):
            for key,child in item.items():
                if not isinstance(key,str) or key.lower().replace('-','_') in _FORBIDDEN:raise ValueError('credentials must not enter local-step storage')
                safe(child)
        elif isinstance(item,(list,tuple)):
            for child in item:safe(child)
    safe(value)
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
    if len(raw.encode('utf-8'))>MAX_RESPONSE_BYTES:raise ValueError('local step response too large')
    return json.loads(raw)


class LocalStepStore:
    def __init__(self,store,principal):
        if not _label(principal.owner_id,160) or not _label(principal.device_id,500):raise ValueError('invalid local-step principal')
        if principal.origin!='desktop':raise DeviceUnavailable()
        self.store,self.principal=store,principal

    def _identity(self,session,turn,step):
        if not _label(session,160) or not _label(turn,160) or not isinstance(step,int) or isinstance(step,bool) or not 0<=step<=2:
            raise ValueError('invalid local-step identity')

    def _guard(self,session):
        db=self.store.db;p=self.principal
        self.store._lock_owner(p.owner_id)
        device=db.scalar(select(Device).where(Device.owner_id==p.owner_id,Device.device_id==p.device_id).with_for_update(read=True).execution_options(populate_existing=True))
        if device is None or device.revoked or device.origin!='desktop':raise DeviceUnavailable()
        if db.scalar(select(DeletedAgentSession.id).where(DeletedAgentSession.owner_id==p.owner_id,DeletedAgentSession.session_id==session)) is not None:raise SessionDeleted()
        pref=db.get(SyncPreferences,p.owner_id,populate_existing=True)
        return pref is None or pref.sync_agent_sessions

    def _rpc(self,operation,session,turn,step,payload):
        p=self.principal
        return self.store.request('rpc/yingxu_local_agent_step','POST',body={'p_owner':p.owner_id,'p_device':p.device_id,'p_origin':p.origin,'p_operation':operation,'p_session':session,'p_turn':turn,'p_step':step,'p_payload':payload})

    def reserve(self,session,turn,step,root_hash,input_hash,parent_nonce=None):
        self._identity(session,turn,step)
        if any(not isinstance(h,str) or re.fullmatch('[0-9a-f]{64}',h) is None for h in (root_hash,input_hash)):
            raise ValueError('invalid local-step hash')
        if (step==0 and parent_nonce is not None) or (step>0 and not _uuid(parent_nonce)):raise ValueError('invalid local-step parent nonce')
        if not isinstance(self.store,SQLStore):return self._rpc('reserve',session,turn,step,{'root_hash':root_hash,'input_hash':input_hash,'parent_nonce':parent_nonce})
        db=self.store.db;p=self.principal
        try:
            retain=self._guard(session)
            if step>0:
                parent=db.get(LocalStep,(p.owner_id,session,turn,step-1),populate_existing=True)
                if (parent is None or parent.device_id!=p.device_id or parent.origin!=p.origin or parent.root_hash!=root_hash
                    or parent.state!='awaiting_tools' or parent.output_nonce!=parent_nonce):raise StoreConflict()
            row=db.get(LocalStep,(p.owner_id,session,turn,step),populate_existing=True)
            if row is not None:
                if row.device_id!=p.device_id or row.origin!=p.origin or row.root_hash!=root_hash or row.input_hash!=input_hash:raise StoreConflict()
                if retain and row.response_json is not None:
                    result={'state':'replayed','response':deepcopy(row.response_json)}
                else:result={'state':row.state,'retained':False}
                db.commit();return result
            attempt=str(uuid4())
            db.add(LocalStep(owner_id=p.owner_id,session_id=session,turn_id=turn,step_index=step,device_id=p.device_id,origin=p.origin,root_hash=root_hash,input_hash=input_hash,attempt_id=attempt,state='pending'))
            db.commit();return {'state':'reserved','attemptId':attempt}
        except Exception:db.rollback();raise

    def finish(self,session,turn,step,attempt,state,response,output_nonce=None):
        self._identity(session,turn,step)
        if not _uuid(attempt) or state not in ('awaiting_tools','completed','uncertain'):raise ValueError('invalid local-step result')
        if (state=='awaiting_tools' and not _uuid(output_nonce)) or (state!='awaiting_tools' and output_nonce is not None):raise ValueError('invalid local-step output nonce')
        safe_response=None if state=='uncertain' else _response(response)
        if not isinstance(self.store,SQLStore):return self._rpc('finish',session,turn,step,{'attempt_id':attempt,'state':state,'response':safe_response,'output_nonce':output_nonce})
        db=self.store.db;p=self.principal
        try:
            retain=self._guard(session)
            row=db.get(LocalStep,(p.owner_id,session,turn,step),populate_existing=True)
            if row is None or row.device_id!=p.device_id or row.origin!=p.origin or row.state!='pending' or row.attempt_id!=attempt:raise StoreConflict()
            row.state=state;row.output_nonce=output_nonce;row.response_json=safe_response if retain else None
            result={'state':state,'retained':row.response_json is not None}
            db.commit();return result
        except Exception:db.rollback();raise
