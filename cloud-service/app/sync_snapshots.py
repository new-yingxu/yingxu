"""Short-lived, principal-bound snapshots of the synced job event log.

Only a committed upper cursor is frozen, never an old copy of event payloads.
Subsequent privacy redaction/deletion therefore remains effective on every page.
This does not snapshot conversations, presets or all account state.
"""
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
from sqlalchemy import BigInteger,DateTime,String,delete,func,select
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .models import Device,SyncEvent,utcnow
from .store import SQLStore,DeviceUnavailable

SNAPSHOT_LIFETIME_SECONDS=300
SNAPSHOT_DEVICE_LIMIT=20
MAX_CURSOR=2**63-1
SCOPE='synced_job_events'


class SnapshotNotFound(ValueError):pass
class SnapshotExpired(ValueError):pass
class SnapshotLimit(ValueError):pass


class SyncSnapshotLease(Base):
    __tablename__='sync_snapshot_leases'
    snapshot_id:Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id:Mapped[str]=mapped_column(String(160),index=True)
    device_id:Mapped[str]=mapped_column(String(500))
    origin:Mapped[str]=mapped_column(String(16))
    through_cursor:Mapped[int]=mapped_column(BigInteger)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)


def _time(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 or ord(c)==127 for c in value)


def _snapshot_id(value):
    try:
        parsed=UUID(value) if isinstance(value,str) else None
        if parsed is None or parsed.version!=4 or str(parsed)!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('invalid snapshot id') from None


class SnapshotStore:
    def __init__(self,store,principal):
        if (not _label(principal.owner_id,160) or not _label(principal.device_id,500)
            or principal.origin not in ('desktop','mobile')):raise ValueError('invalid snapshot principal')
        self.store,self.principal=store,principal

    def _guard(self):
        db=self.store.db;p=self.principal
        self.store._lock_owner(p.owner_id)
        device=db.scalar(select(Device).where(Device.owner_id==p.owner_id,Device.device_id==p.device_id).with_for_update(read=True).execution_options(populate_existing=True))
        if device is None or device.revoked or device.origin!=p.origin:raise DeviceUnavailable()

    def _scope(self):
        p=self.principal
        return (SyncSnapshotLease.owner_id==p.owner_id,SyncSnapshotLease.device_id==p.device_id,SyncSnapshotLease.origin==p.origin)

    def _rpc(self,operation,**values):
        p=self.principal
        result=self.store.request('rpc/yingxu_sync_snapshot','POST',body={'p_owner':p.owner_id,'p_device':p.device_id,'p_origin':p.origin,'p_operation':operation,**values})
        if isinstance(result,dict) and 'error' in result:
            error={'SNAPSHOT_NOT_FOUND':SnapshotNotFound,'SNAPSHOT_EXPIRED':SnapshotExpired,'SNAPSHOT_LIMIT':SnapshotLimit}.get(result['error'])
            if error:raise error()
            raise ValueError('invalid snapshot response')
        if not isinstance(result,dict) or result.get('scope')!=SCOPE:raise ValueError('invalid snapshot response')
        result['expiresAt']=_time(result['expiresAt']).isoformat()
        return result

    def begin(self):
        if not isinstance(self.store,SQLStore):return self._rpc('begin')
        db=self.store.db;p=self.principal
        try:
            self._guard();now=utcnow()
            # Expired metadata may be reclaimed across this owner's devices only.
            db.execute(delete(SyncSnapshotLease).where(SyncSnapshotLease.owner_id==p.owner_id,SyncSnapshotLease.expires_at<=now))
            count=db.scalar(select(func.count()).select_from(SyncSnapshotLease).where(*self._scope()))
            if count>=SNAPSHOT_DEVICE_LIMIT:raise SnapshotLimit()
            through=db.scalar(select(func.max(SyncEvent.id)).where(SyncEvent.owner_id==p.owner_id)) or 0
            lease=SyncSnapshotLease(snapshot_id=str(uuid4()),owner_id=p.owner_id,device_id=p.device_id,origin=p.origin,
                                    through_cursor=through,expires_at=now+timedelta(seconds=SNAPSHOT_LIFETIME_SECONDS))
            db.add(lease);db.flush()
            result={'snapshotId':lease.snapshot_id,'throughCursor':through,'expiresAt':_time(lease.expires_at).isoformat(),'scope':SCOPE}
            db.commit();return result
        except Exception:db.rollback();raise

    def page(self,snapshot_id,after=0,limit=100):
        _snapshot_id(snapshot_id)
        if (not isinstance(after,int) or isinstance(after,bool) or not 0<=after<=MAX_CURSOR
            or not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=500):raise ValueError('invalid snapshot cursor or limit')
        if not isinstance(self.store,SQLStore):return self._rpc('page',p_snapshot=snapshot_id,p_after=after,p_limit=limit)
        db=self.store.db;p=self.principal
        try:
            self._guard()
            lease=db.scalar(select(SyncSnapshotLease).where(*self._scope(),SyncSnapshotLease.snapshot_id==snapshot_id))
            if lease is None:raise SnapshotNotFound()
            if _time(lease.expires_at)<=utcnow():raise SnapshotExpired()
            if after>lease.through_cursor:raise ValueError('snapshot cursor exceeds upper boundary')
            rows=db.scalars(select(SyncEvent).where(SyncEvent.owner_id==p.owner_id,SyncEvent.id>after,
                SyncEvent.id<=lease.through_cursor).order_by(SyncEvent.id).limit(limit+1)).all()
            page=rows[:limit]
            result={'snapshotId':snapshot_id,'throughCursor':lease.through_cursor,'expiresAt':_time(lease.expires_at).isoformat(),
                    'events':[row.body_json for row in page],'nextCursor':page[-1].id if page else after,'hasMore':len(rows)>limit,'scope':SCOPE}
            db.commit();return result
        except Exception:db.rollback();raise
