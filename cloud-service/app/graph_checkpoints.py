"""Principal-bound synchronous LangGraph saver. Each operation owns its DB session."""
import base64
from dataclasses import fields, is_dataclass
import json
from sqlalchemy import JSON, Integer, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from .db import Base
from .models import DeletedAgentSession, SyncPreferences
from .store import SQLStore, DeviceUnavailable, SessionDeleted, SyncForbidden

MAX_BLOB = 2 * 1024 * 1024
_SECRET_KEYS = {'authorization', 'access_token', 'refresh_token', 'api_key', 'apikey', 'password', 'secret', 'token', 'service_role_key', 'credentials'}


def _safe(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace('-', '_') in _SECRET_KEYS:
                raise ValueError('Credentials must not enter graph state')
            _safe(child)
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            if field.name.lower() in _SECRET_KEYS: raise ValueError('Credentials must not enter graph state')
            _safe(getattr(value, field.name))
    elif hasattr(value, 'model_dump'):
        _safe(value.model_dump())
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child)


class GraphCheckpoint(Base):
    __tablename__ = 'agent_graph_checkpoints'
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(500), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class GraphPendingWrite(Base):
    __tablename__ = 'agent_graph_pending_writes'
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(500), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class PrincipalCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, store_factory, *, owner_id, device_id, origin, session_id, thread_id):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        for value, maximum in ((owner_id,160),(device_id,500),(origin,16),(session_id,160),(thread_id,160)):
            if not isinstance(value,str) or not 0 < len(value) <= maximum:
                raise ValueError('Invalid checkpoint principal')
        self.store_factory = store_factory
        self.owner_id, self.device_id, self.origin = owner_id, device_id, origin
        self.session_id, self.thread_id = session_id, thread_id

    def _config(self, config):
        c = (config or {}).get('configurable', {})
        if c.get('thread_id', self.thread_id) != self.thread_id:
            raise ValueError('Cross-thread checkpoint access')
        # LangGraph injects internal runtime fields; none are persisted.
        if any(k in c for k in ('owner_id','device_id','origin','session_id')):
            raise ValueError('Principal cannot be supplied through graph config')
        ns, cid = c.get('checkpoint_ns', ''), c.get('checkpoint_id')
        if not isinstance(ns,str) or len(ns)>500 or (cid is not None and (not isinstance(cid,str) or len(cid)>160)):
            raise ValueError('Invalid checkpoint config')
        return ns, cid

    def _out(self, ns, cid):
        return {'configurable': {'thread_id': self.thread_id, 'checkpoint_ns': ns, 'checkpoint_id': cid}}

    def _dump(self, value):
        _safe(value)
        kind, blob = self.serde.dumps_typed(value)
        if kind == 'pickle' or len(blob)>MAX_BLOB:
            raise ValueError('Unsafe or oversized checkpoint')
        return {'type':kind,'data':base64.b64encode(blob).decode('ascii')}

    def _load(self, value):
        if set(value) != {'type','data'} or value['type'] not in {'json','msgpack','bytes','null'} or len(value['data']) > (MAX_BLOB+2)//3*4:
            raise ValueError('Invalid checkpoint encoding')
        blob = base64.b64decode(value['data'], validate=True)
        if len(blob)>MAX_BLOB: raise ValueError('Oversized checkpoint')
        result = self.serde.loads_typed((value['type'],blob))
        _safe(result)
        return result

    def _scope(self, model, ns=None):
        clauses=[model.owner_id==self.owner_id,model.session_id==self.session_id,model.thread_id==self.thread_id]
        if ns is not None: clauses.append(model.checkpoint_ns==ns)
        return clauses

    def _guard(self, store):
        store._lock_owner(self.owner_id)
        device=store.device(self.owner_id,self.device_id)
        if device is None or device.revoked or device.origin!=self.origin: raise DeviceUnavailable()
        if store.db.scalar(select(DeletedAgentSession.id).where(DeletedAgentSession.owner_id==self.owner_id,DeletedAgentSession.session_id==self.session_id)) is not None: raise SessionDeleted()
        pref=store.db.get(SyncPreferences,self.owner_id)
        return pref is None or pref.sync_agent_sessions

    def _operation(self, operation, ns='', cid=None, payload=None):
        with self.store_factory() as store:
            if not isinstance(store, SQLStore):
                result = store.request('rpc/yingxu_graph_checkpoint','POST',body={'p_owner':self.owner_id,'p_device':self.device_id,'p_origin':self.origin,'p_session':self.session_id,'p_thread':self.thread_id,'p_ns':ns,'p_checkpoint':cid,'p_operation':operation,'p_payload':payload or {}})
                return (result or []) if operation=='read' else result
            try:
                if not self._guard(store):
                    store.db.rollback()
                    return [] if operation=='read' else None
                if operation=='read':
                    rows=store.db.scalars(select(GraphCheckpoint).where(*self._scope(GraphCheckpoint,ns)).order_by(GraphCheckpoint.checkpoint_id.desc())).all()
                    result=[]
                    for row in rows:
                        if cid is not None and row.checkpoint_id!=cid: continue
                        writes=store.db.scalars(select(GraphPendingWrite).where(*self._scope(GraphPendingWrite,row.checkpoint_ns),GraphPendingWrite.checkpoint_id==row.checkpoint_id)).all()
                        result.append({'checkpoint_ns':row.checkpoint_ns,'checkpoint_id':row.checkpoint_id,'payload':row.payload,'writes':[dict(w.payload,task_id=w.task_id,idx=w.idx) for w in writes]})
                elif operation=='put':
                    key=(self.owner_id,self.session_id,self.thread_id,ns,cid)
                    row=store.db.get(GraphCheckpoint,key)
                    if row is None: store.db.add(GraphCheckpoint(owner_id=self.owner_id,session_id=self.session_id,thread_id=self.thread_id,checkpoint_ns=ns,checkpoint_id=cid,payload=payload))
                    else: row.payload=payload
                    result=None
                elif operation=='writes':
                    for w in payload['writes']:
                        key=(self.owner_id,self.session_id,self.thread_id,ns,cid,w['task_id'],w['idx'])
                        row=store.db.get(GraphPendingWrite,key)
                        body={k:w[k] for k in ('channel','value','task_path')}
                        if row is None: store.db.add(GraphPendingWrite(owner_id=self.owner_id,session_id=self.session_id,thread_id=self.thread_id,checkpoint_ns=ns,checkpoint_id=cid,task_id=w['task_id'],idx=w['idx'],payload=body))
                        elif w['idx']<0: row.payload=body
                    result=None
                elif operation=='delete':
                    for model in (GraphPendingWrite,GraphCheckpoint): store.db.execute(delete(model).where(*self._scope(model)))
                    result=None
                else: raise ValueError('Unknown checkpoint operation')
                store.db.commit()
                return result
            except Exception:
                store.db.rollback()
                raise

    def _tuple(self, ns, row):
        ns=row.get('checkpoint_ns',ns)
        p=row['payload']
        writes=sorted(row['writes'],key=lambda w:(w['task_path'],w['task_id'],w['idx']))
        return CheckpointTuple(self._out(ns,row['checkpoint_id']),self._load(p['checkpoint']),self._load(p['metadata']),self._out(ns,p['parent']) if p['parent'] else None,[(w['task_id'],w['channel'],self._load(w['value'])) for w in writes])

    def get_tuple(self, config):
        ns,cid=self._config(config)
        rows=self._operation('read',ns,cid)
        return self._tuple(ns,rows[0]) if rows else None

    def list(self, config, *, filter=None, before=None, limit=None):
        ns,cid=self._config(config)
        before_id=self._config(before)[1] if before else None
        if limit is not None and limit<0: raise ValueError('Invalid limit')
        rows=self._operation('read',None if config is None else ns,cid)
        results=[]
        for row in rows:
            if before_id and row['checkpoint_id']>=before_id: continue
            item=self._tuple(ns,row)
            if filter and any(item.metadata.get(k)!=v for k,v in filter.items()): continue
            results.append(item)
        return iter(results if limit is None else results[:limit])

    def put(self, config, checkpoint, metadata, new_versions):
        ns,parent=self._config(config)
        cid=checkpoint['id']
        self._config(self._out(ns,cid))
        self._operation('put',ns,cid,{'checkpoint':self._dump(checkpoint),'metadata':self._dump(metadata),'parent':parent})
        return self._out(ns,cid)

    def put_writes(self, config, writes, task_id, task_path=''):
        ns,cid=self._config(config)
        if not cid or not isinstance(task_id,str) or len(task_id)>160 or not isinstance(task_path,str) or len(task_path)>1000: raise ValueError('Invalid pending write')
        body=[{'task_id':task_id,'idx':WRITES_IDX_MAP.get(channel,i),'channel':channel,'task_path':task_path,'value':self._dump(value)} for i,(channel,value) in enumerate(writes)]
        self._operation('writes',ns,cid,{'writes':body})

    def delete_thread(self, thread_id):
        self._config({'configurable':{'thread_id':thread_id}})
        self._operation('delete')
