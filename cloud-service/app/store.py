"""Owner-scoped persistence for local SQLAlchemy and CloudBase PostgREST."""
from datetime import datetime
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from sqlalchemy import select, update, text
from sqlalchemy.exc import IntegrityError

from .models import Device, SyncEvent, AgentAction, SyncPreferences, utcnow


class StoreConflict(ValueError):
    pass


class StoreUnavailable(RuntimeError):
    pass


class DeviceUnavailable(ValueError):
    pass


class SQLStore:
    def __init__(self, session):
        self.db = session

    def close(self):
        self.db.close()

    def ready(self, production=False):
        self.db.execute(text("SELECT 1"))
        return not production or self.db.execute(text("SELECT version FROM schema_migrations WHERE version=3")).first() is not None

    def device(self, owner, device):
        return self.db.scalar(select(Device).where(Device.owner_id == owner, Device.device_id == device))

    def devices(self, owner):
        return self.db.scalars(select(Device).where(Device.owner_id == owner).order_by(Device.created_at)).all()

    def save_device(self, row):
        self.db.add(row)
        self.db.commit()

    def touch_device(self, row):
        result = self.db.execute(update(Device).where(Device.owner_id == row.owner_id, Device.device_id == row.device_id, Device.revoked.is_(False), Device.origin == row.origin).values(last_seen_at=utcnow()).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            raise DeviceUnavailable()
        self.db.commit()

    def revoke_device(self, row):
        row.revoked = True
        self.db.commit()

    def preferences(self, owner):
        return self.db.get(SyncPreferences, owner)

    def set_preferences(self, owner, values):
        row = self.preferences(owner) or SyncPreferences(owner_id=owner)
        for k, v in values.items():
            setattr(row, k, v)
        self.db.add(row)
        if not values['sync_paths']:
            for event in self.db.scalars(select(SyncEvent).where(SyncEvent.owner_id == owner)).all():
                body = dict(event.body_json)
                body['payload'] = {k: v for k, v in body['payload'].items() if k not in {'source', 'paths'}}
                event.body_json = body
        self.db.commit()

    def existing_event(self, owner, event_id, device, sequence):
        return self.db.scalar(select(SyncEvent).where(SyncEvent.owner_id == owner, SyncEvent.event_id == event_id)) or self.db.scalar(select(SyncEvent).where(SyncEvent.owner_id == owner, SyncEvent.device_id == device, SyncEvent.sequence == sequence))

    def events(self, owner, after=0, limit=100, newest=False):
        order = SyncEvent.id.desc() if newest else SyncEvent.id
        return self.db.scalars(select(SyncEvent).where(SyncEvent.owner_id == owner, SyncEvent.id > after).order_by(order).limit(limit)).all()

    def _add(self, row):
        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise StoreConflict() from exc

    def add_event(self, row):
        self._add(row)

    def action(self, owner, action_id):
        return self.db.scalar(select(AgentAction).where(AgentAction.owner_id == owner, AgentAction.id == action_id))

    def add_action(self, row):
        self._add(row)

    def transition_action(self, row, new_state):
        result = self.db.execute(update(AgentAction).where(AgentAction.owner_id == row.owner_id, AgentAction.id == row.id, AgentAction.state == row.state).values(state=new_state).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            raise StoreConflict()
        self.db.commit()
        row.state = new_state


def _literal(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _row(model, values):
    values = dict(values)
    for name in ('created_at', 'last_seen_at', 'expires_at', 'updated_at'):
        if isinstance(values.get(name), str):
            values[name] = datetime.fromisoformat(values[name].replace('Z', '+00:00'))
    return model(**values)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError('unsupported persistence value')


class PostgRESTStore:
    def __init__(self, env_id, api_key):
        if not env_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in env_id):
            raise StoreUnavailable('invalid CloudBase environment')
        self.base = f'https://{env_id}.api.tcloudbasegateway.com/v1/rdb/rest/'
        self.key = api_key

    def close(self):
        pass

    def request(self, table, method='GET', query=None, body=None):
        url = self.base + table + ('?' + urlencode(query) if query else '')
        data = None if body is None else json.dumps(body, default=_json_default).encode()
        req = Request(url, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.key,
            'Content-Type': 'application/json',
            'Prefer': 'return=representation',
        })
        try:
            with urlopen(req, timeout=12) as response:
                raw = response.read()
                return json.loads(raw) if raw else []
        except HTTPError as exc:
            if exc.code == 409:
                raise StoreConflict() from exc
            raise StoreUnavailable('CloudBase database request failed') from None
        except (URLError, TimeoutError, ValueError):
            raise StoreUnavailable('CloudBase database unavailable') from None

    def ready(self, production=False):
        return bool(self.request('schema_migrations', query={'select': 'version', 'version': 'eq.3', 'limit': '1'}))

    def _filters(self, owner, **fields):
        return {'owner_id': 'eq.' + _literal(owner), **{k: 'eq.' + _literal(v) for k, v in fields.items()}}

    def device(self, owner, device):
        rows = self.request('devices', query={**self._filters(owner, device_id=device), 'limit': 1})
        return _row(Device, rows[0]) if rows else None

    def devices(self, owner):
        rows = self.request('devices', query={**self._filters(owner), 'order': 'created_at.asc', 'limit': 500})
        return [_row(Device, v) for v in rows]

    def save_device(self, row):
        body = {k: getattr(row, k) for k in ('owner_id', 'device_id', 'origin', 'display_name')}
        body.update(revoked=bool(row.revoked), last_seen_at=row.last_seen_at or utcnow())
        if row.id is None:
            body['created_at'] = row.created_at or utcnow()
            result = self.request('devices', 'POST', body=body)
            row.id = result[0]['id']
        else:
            body.pop('revoked')
            body.pop('origin')
            result = self.request('devices', 'PATCH', {**self._filters(row.owner_id, device_id=row.device_id, origin=row.origin), 'revoked': 'eq.false'}, body)
            if not result:
                raise DeviceUnavailable()

    def touch_device(self, row):
        rows = self.request('devices', 'PATCH', {**self._filters(row.owner_id, device_id=row.device_id, origin=row.origin), 'revoked': 'eq.false'}, {'last_seen_at': utcnow()})
        if not rows:
            raise DeviceUnavailable()

    def revoke_device(self, row):
        self.request('devices', 'PATCH', self._filters(row.owner_id, device_id=row.device_id), {'revoked': True})
        row.revoked = True

    def preferences(self, owner):
        rows = self.request('sync_preferences', query={**self._filters(owner), 'limit': 1})
        return _row(SyncPreferences, rows[0]) if rows else None

    def set_preferences(self, owner, values):
        self.request('rpc/yingxu_set_preferences', 'POST', body={'p_owner': owner, **{'p_' + k: v for k, v in values.items()}})

    def existing_event(self, owner, event_id, device, sequence):
        rows = self.request('sync_events', query={**self._filters(owner, event_id=event_id), 'limit': 1})
        if not rows:
            rows = self.request('sync_events', query={**self._filters(owner, device_id=device), 'sequence': 'eq.' + str(sequence), 'limit': 1})
        return _row(SyncEvent, rows[0]) if rows else None

    def events(self, owner, after=0, limit=100, newest=False):
        rows = self.request('sync_events', query={**self._filters(owner), 'id': 'gt.' + str(after), 'order': 'id.desc' if newest else 'id.asc', 'limit': limit})
        return [_row(SyncEvent, v) for v in rows]

    def add_event(self, row):
        body = {k: getattr(row, k) for k in ('owner_id', 'device_id', 'event_id', 'sequence', 'body_json')}
        body['created_at'] = row.created_at or utcnow()
        result = self.request('sync_events', 'POST', body=body)
        row.id = result[0]['id']

    def action(self, owner, action_id):
        rows = self.request('agent_actions', query={**self._filters(owner, id=action_id), 'limit': 1})
        return _row(AgentAction, rows[0]) if rows else None

    def add_action(self, row):
        body = {k: getattr(row, k) for k in ('id', 'owner_id', 'device_id', 'origin', 'tool', 'state', 'proposal_json', 'expires_at')}
        body['created_at'] = row.created_at or utcnow()
        self.request('agent_actions', 'POST', body=body)

    def transition_action(self, row, new_state):
        rows = self.request('agent_actions', 'PATCH', self._filters(row.owner_id, id=row.id, state=row.state), {'state': new_state})
        if len(rows) != 1:
            raise StoreConflict()
        row.state = new_state
