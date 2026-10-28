"""Explicit owner-wide sync-content erasure, not account/source-file deletion.

Security-only IDs, hashes, reservations, revocation rows and empty tombstones
remain to prevent revoked clients from republishing or repeating paid requests.
"""
from copy import deepcopy
from datetime import datetime
from sqlalchemy import DateTime,JSON,String,delete,func,select,update
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .models import (Device,SyncPreferences,SyncEvent,AgentTurn,AgentAction,DeletedAgentSession,
                     AgentModelRequest,SyncInvalidation,utcnow)
from .graph_checkpoints import GraphCheckpoint,GraphPendingWrite
from .preset_store import PresetRevision
from .store import SQLStore,StoreConflict,DeviceUnavailable
from .local_step_store import LocalStep,LOCAL_STEP_SECURITY_METADATA


RETAINED_SECURITY_METADATA=[
    {'kind':'device_revocations','reason':'Keep device IDs/origins and revocation state so old sessions cannot republish; display names are erased.'},
    {'kind':'model_request_deduplication','reason':'Keep request/session/turn/device IDs, query hashes, attempt IDs, states and timestamps; do not repeat billed or uncertain calls.'},
    {'kind':'session_tombstones','reason':'Keep session IDs and deletion timestamps to block recreation of erased agent history.'},
    {'kind':'empty_preset_tombstones','reason':'Keep preset IDs/versions and empty deleted revisions so clients can remove stale local copies.'},
    {'kind':'cleanup_receipts_and_owner_lock','reason':'Keep request/device/origin IDs, counts and fixed explanations for idempotency and serialized revocation.'},
    {'kind':'disabled_preferences_and_invalidation','reason':'Keep all sync flags disabled and one deletion notification; a new login must explicitly enable sync.'},
]


class DataCleanupReceipt(Base):
    __tablename__='data_cleanup_receipts'
    owner_id:Mapped[str]=mapped_column(String(160),primary_key=True)
    request_id:Mapped[str]=mapped_column(String(160),primary_key=True)
    device_id:Mapped[str]=mapped_column(String(500))
    origin:Mapped[str]=mapped_column(String(16))
    result_json:Mapped[dict]=mapped_column(JSON)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 for c in value)


class AccountDataStore:
    def __init__(self,store,principal):
        if (not _label(principal.owner_id,160) or not _label(principal.device_id,500)
            or principal.origin not in ('desktop','mobile')):raise ValueError('invalid cleanup principal')
        self.store,self.principal=store,principal

    def clear(self,request_id,confirmed):
        if confirmed is not True or not _label(request_id,160):raise ValueError('explicit cleanup confirmation and request ID required')
        p=self.principal
        if not isinstance(self.store,SQLStore):
            return self.store.request('rpc/yingxu_clear_sync_data','POST',body={'p_owner':p.owner_id,'p_device':p.device_id,'p_origin':p.origin,'p_request':request_id,'p_confirmed':True})
        db=self.store.db
        try:
            self.store._lock_owner(p.owner_id)
            prior=db.get(DataCleanupReceipt,(p.owner_id,request_id),populate_existing=True)
            if prior is not None:
                if prior.device_id!=p.device_id or prior.origin!=p.origin:raise StoreConflict()
                result=deepcopy(prior.result_json);db.commit();return result
            device=db.scalar(select(Device).where(Device.owner_id==p.owner_id,Device.device_id==p.device_id).with_for_update(read=True).execution_options(populate_existing=True))
            if device is None or device.revoked or device.origin!=p.origin:raise DeviceUnavailable()
            counts={}
            sessions=set()
            for model in (AgentTurn,AgentModelRequest,GraphCheckpoint,GraphPendingWrite,LocalStep):
                sessions.update(db.scalars(select(model.session_id).where(model.owner_id==p.owner_id)).all())
            existing=set(db.scalars(select(DeletedAgentSession.session_id).where(DeletedAgentSession.owner_id==p.owner_id)).all())
            for session in sorted(sessions-existing):db.add(DeletedAgentSession(owner_id=p.owner_id,session_id=session))
            counts['sessionTombstonesAdded']=len(sessions-existing)
            for model,key in ((SyncEvent,'syncEvents'),(AgentTurn,'agentTurns'),(AgentAction,'agentActions'),
                              (GraphPendingWrite,'graphPendingWrites'),(GraphCheckpoint,'graphCheckpoints')):
                counts[key]=db.execute(delete(model).where(model.owner_id==p.owner_id)).rowcount
            db.execute(update(LocalStep).where(LocalStep.owner_id==p.owner_id).values(response_json=None))
            # Ephemeral snapshot handles contain no payload, but clearing them
            # removes obsolete owner/device links as part of content cleanup.
            from .sync_snapshots import SyncSnapshotLease
            db.execute(delete(SyncSnapshotLease).where(SyncSnapshotLease.owner_id==p.owner_id))
            counts['modelRequestsRetained']=db.scalar(select(func.count()).select_from(AgentModelRequest).where(AgentModelRequest.owner_id==p.owner_id))
            db.execute(update(AgentModelRequest).where(AgentModelRequest.owner_id==p.owner_id,AgentModelRequest.state=='completed').values(state='completed_unretained'))
            latest_ids=select(func.max(PresetRevision.id)).where(PresetRevision.owner_id==p.owner_id).group_by(PresetRevision.preset_id)
            active=[(r.preset_id,r.version) for r in db.scalars(select(PresetRevision).where(PresetRevision.owner_id==p.owner_id,PresetRevision.id.in_(latest_ids),PresetRevision.deleted.is_(False))).all()]
            counts['presetRevisionsCleared']=db.execute(update(PresetRevision).where(PresetRevision.owner_id==p.owner_id).values(body_json={},deleted=True)).rowcount
            for preset,version in sorted(active):db.add(PresetRevision(owner_id=p.owner_id,preset_id=preset,version=version+1,body_json={},deleted=True))
            counts['presetTombstonesAdded']=len(active)
            counts['previousInvalidationsRemoved']=db.execute(delete(SyncInvalidation).where(SyncInvalidation.owner_id==p.owner_id)).rowcount
            db.add(SyncInvalidation(owner_id=p.owner_id,kind='all_sync_data_deleted'))
            devices_revoked=db.scalar(select(func.count()).select_from(Device).where(Device.owner_id==p.owner_id,Device.revoked.is_(False)))
            db.execute(update(Device).where(Device.owner_id==p.owner_id).values(revoked=True,display_name=''))
            prefs=db.get(SyncPreferences,p.owner_id,populate_existing=True) or SyncPreferences(owner_id=p.owner_id)
            for flag in ('sync_records','sync_agent_sessions','sync_thumbnails','sync_paths'):setattr(prefs,flag,False)
            db.add(prefs)
            result={'cleared':True,'counts':counts,'devicesRevoked':devices_revoked,'retainedSecurityMetadata':deepcopy(RETAINED_SECURITY_METADATA)}
            result['retainedSecurityMetadata'].append(deepcopy(LOCAL_STEP_SECURITY_METADATA))
            db.add(DataCleanupReceipt(owner_id=p.owner_id,request_id=request_id,device_id=p.device_id,origin=p.origin,result_json=deepcopy(result)))
            db.commit();return result
        except Exception:db.rollback();raise
