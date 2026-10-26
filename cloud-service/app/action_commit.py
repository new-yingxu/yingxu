"""Owner-serialized proposal insertion; prevents late writes after revocation."""
from datetime import datetime,timezone
import json
from sqlalchemy import select
from .models import Device
from .store import DeviceUnavailable


INITIAL_ACTION_STATES={'proposed','awaiting_local_confirmation'}
MAX_ACTION_BYTES=2*1024*1024


def _label(value,maximum):
    return isinstance(value,str) and 1<=len(value)<=maximum and bool(value.strip()) and not any(ord(c)<32 or ord(c)==127 for c in value)


def commit_action(store,row):
    """Insert under the same owner lock as account cleanup; does not deduplicate.

    The endpoint owns frozen proposal-schema validation and duplicate semantics.
    Only the two existing initial states may be inserted by this boundary.
    """
    from .store import SQLStore
    state=row.state if row.state is not None else 'proposed'
    if (not _label(row.owner_id,160) or not _label(row.device_id,500) or row.origin not in ('desktop','mobile')
        or not _label(row.id,500) or not _label(row.tool,64) or state not in INITIAL_ACTION_STATES
        or not isinstance(row.proposal_json,dict) or not isinstance(row.expires_at,datetime)):
        raise ValueError('invalid initial action')
    # Validate JSON serialization without imposing a second proposal schema.
    encoded=json.dumps(row.proposal_json,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8'))>MAX_ACTION_BYTES:raise ValueError('action proposal too large')
    row.state=state
    if not isinstance(store,SQLStore):
        expires=row.expires_at if row.expires_at.tzinfo is not None else row.expires_at.replace(tzinfo=timezone.utc)
        store.request('rpc/yingxu_add_action','POST',body={'p_owner':row.owner_id,'p_device':row.device_id,'p_origin':row.origin,
            'p_action':{'id':row.id,'tool':row.tool,'state':state,'proposal_json':row.proposal_json,'expires_at':expires.isoformat()}})
        return
    try:
        store._lock_owner(row.owner_id)
        device=store.db.scalar(select(Device).where(Device.owner_id==row.owner_id,Device.device_id==row.device_id).with_for_update(read=True).execution_options(populate_existing=True))
        if device is None or device.revoked or device.origin!=row.origin:raise DeviceUnavailable()
        store._add(row)
    except Exception:
        store.db.rollback()
        raise
