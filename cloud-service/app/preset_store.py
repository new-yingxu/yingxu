"""Append-only owner-scoped name presets. No file contents or path grants."""
from datetime import datetime,timezone
from sqlalchemy import Boolean,DateTime,Integer,JSON,String,UniqueConstraint,func,select,update
from sqlalchemy.orm import Mapped,mapped_column
from pydantic import BaseModel,ConfigDict,Field
from .db import Base
from .models import Device,SyncPreferences,utcnow
from .store import SQLStore,DeviceUnavailable,StoreConflict,SyncForbidden


class PresetConflict(ValueError):pass
class PresetNotFound(ValueError):pass
class PresetLimit(ValueError):pass
class PresetSyncDisabled(ValueError):pass


class PresetBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    name:str=Field(min_length=1,max_length=120)
    pattern:str=Field(min_length=1,max_length=500)
    isDefault:bool


class PresetRevision(Base):
    __tablename__='preset_revisions'
    __table_args__=(UniqueConstraint('owner_id','preset_id','version',name='uq_preset_revision'),)
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    owner_id:Mapped[str]=mapped_column(String(160),index=True)
    preset_id:Mapped[str]=mapped_column(String(160))
    version:Mapped[int]=mapped_column(Integer)
    body_json:Mapped[dict]=mapped_column(JSON)
    deleted:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


def _revision(row):
    created=row.created_at
    if created.tzinfo is None:created=created.replace(tzinfo=timezone.utc)
    return {'id':row.id,'presetId':row.preset_id,'version':row.version,'body':dict(row.body_json),
            'deleted':row.deleted,'createdAt':created.astimezone(timezone.utc).isoformat()}


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 for c in value)


class PresetStore:
    def __init__(self,store,principal):
        if (not _label(principal.owner_id,160) or not _label(principal.device_id,500)
            or principal.origin not in ('desktop','mobile')):raise ValueError('invalid preset principal')
        self.store,self.principal=store,principal

    def _guard(self,write):
        db=self.store.db;p=self.principal
        self.store._lock_owner(p.owner_id)
        device=db.scalar(select(Device).where(Device.owner_id==p.owner_id,Device.device_id==p.device_id).with_for_update(read=True).execution_options(populate_existing=True))
        if device is None or device.revoked or device.origin!=p.origin:raise DeviceUnavailable()
        pref=db.get(SyncPreferences,p.owner_id,populate_existing=True)
        if write and pref is not None and not pref.sync_records:raise PresetSyncDisabled()

    def _rpc(self,operation,**values):
        p=self.principal
        try:
            result=self.store.request('rpc/yingxu_presets','POST',body={'p_owner':p.owner_id,'p_device':p.device_id,'p_origin':p.origin,'p_operation':operation,**values})
        except StoreConflict:raise PresetConflict() from None
        except SyncForbidden:raise PresetSyncDisabled() from None
        if isinstance(result,dict) and 'error' in result:
            error={'PRESET_CONFLICT':PresetConflict,'PRESET_NOT_FOUND':PresetNotFound,'PRESET_LIMIT':PresetLimit,'PRESET_SYNC_DISABLED':PresetSyncDisabled}.get(result['error'])
            if error:raise error()
            raise ValueError('invalid preset result')
        if isinstance(result,dict):
            revisions=[result['revision']] if 'revision' in result else result.get('changes',[])
            for revision in revisions:
                if isinstance(revision.get('createdAt'),str):
                    stamp=datetime.fromisoformat(revision['createdAt'].replace('Z','+00:00'))
                    if stamp.tzinfo is None:raise ValueError('preset timestamp requires timezone')
                    revision['createdAt']=stamp.astimezone(timezone.utc).isoformat()
        return result

    def changes(self,after=0,limit=100):
        if (not isinstance(after,int) or isinstance(after,bool) or not 0<=after<=2**63-1
            or not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100):raise ValueError('invalid preset cursor')
        if not isinstance(self.store,SQLStore):return self._rpc('changes',p_after=after,p_limit=limit)
        db=self.store.db
        try:
            self._guard(False)
            rows=db.scalars(select(PresetRevision).where(PresetRevision.owner_id==self.principal.owner_id,PresetRevision.id>after).order_by(PresetRevision.id).limit(limit+1)).all()
            page=rows[:limit];result={'changes':[_revision(r) for r in page],'nextCursor':page[-1].id if page else after,'hasMore':len(rows)>limit}
            db.commit();return result
        except Exception:db.rollback();raise

    def _identity(self,preset_id,expected_version):
        if not _label(preset_id,160) or not isinstance(expected_version,int) or isinstance(expected_version,bool) or not 0<=expected_version<2**31-1:
            raise ValueError('invalid preset id or expected version')

    def _latest(self,preset_id):
        return self.store.db.scalar(select(PresetRevision).where(PresetRevision.owner_id==self.principal.owner_id,PresetRevision.preset_id==preset_id).order_by(PresetRevision.version.desc()).limit(1))

    def _active(self):
        owner=self.principal.owner_id
        ids=select(func.max(PresetRevision.id)).where(PresetRevision.owner_id==owner).group_by(PresetRevision.preset_id)
        return self.store.db.scalars(select(PresetRevision).where(PresetRevision.owner_id==owner,PresetRevision.id.in_(ids),PresetRevision.deleted.is_(False))).all()

    def _append(self,preset_id,version,body,deleted=False):
        row=PresetRevision(owner_id=self.principal.owner_id,preset_id=preset_id,version=version,body_json=body,deleted=deleted)
        self.store.db.add(row);self.store.db.flush();return row

    def put(self,preset_id,body,expected_version):
        self._identity(preset_id,expected_version)
        body=PresetBody.model_validate(body).model_dump()
        if not body['name'].strip() or not body['pattern'].strip():raise ValueError('empty preset rule')
        if not isinstance(self.store,SQLStore):return self._rpc('put',p_preset=preset_id,p_body=body,p_expected=expected_version)
        db=self.store.db
        try:
            self._guard(True);prior=self._latest(preset_id)
            if prior is not None and prior.version==expected_version+1 and not prior.deleted and prior.body_json==body:
                result={'revision':_revision(prior),'duplicate':True};db.commit();return result
            if (prior.version if prior else 0)!=expected_version:raise PresetConflict()
            active=self._active()
            if (prior is None or prior.deleted) and len(active)>=100:raise PresetLimit()
            if body['isDefault']:
                for other in sorted(active,key=lambda row:row.id):
                    if other.preset_id!=preset_id and other.body_json['isDefault']:
                        self._append(other.preset_id,other.version+1,{**other.body_json,'isDefault':False})
            row=self._append(preset_id,expected_version+1,body)
            result={'revision':_revision(row),'duplicate':False};db.commit();return result
        except Exception:db.rollback();raise

    def delete(self,preset_id,expected_version,confirmed):
        self._identity(preset_id,expected_version)
        if confirmed is not True:raise ValueError('explicit preset deletion confirmation required')
        if not isinstance(self.store,SQLStore):return self._rpc('delete',p_preset=preset_id,p_expected=expected_version,p_confirmed=True)
        db=self.store.db
        try:
            self._guard(True);prior=self._latest(preset_id)
            if prior is None:raise PresetNotFound()
            if prior.deleted and prior.version==expected_version+1:
                result={'revision':_revision(prior),'duplicate':True};db.commit();return result
            if prior.deleted or prior.version!=expected_version:raise PresetConflict()
            db.execute(update(PresetRevision).where(PresetRevision.owner_id==self.principal.owner_id,PresetRevision.preset_id==preset_id).values(body_json={},deleted=True))
            row=self._append(preset_id,expected_version+1,{},True)
            result={'revision':_revision(row),'duplicate':False};db.commit();return result
        except Exception:db.rollback();raise
