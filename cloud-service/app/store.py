"""Owner-scoped persistence for local SQLAlchemy and CloudBase PostgREST."""
from datetime import datetime
import json
import hashlib
from uuid import uuid4
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError

from .config import REQUIRED_SCHEMA_VERSION
from .models import Device, SyncEvent, AgentAction, SyncPreferences, AgentTurn, DeletedAgentSession, SyncInvalidation, AgentModelRequest, utcnow


class StoreConflict(ValueError):
    pass


class SessionDeleted(ValueError):
    pass


class StoreUnavailable(RuntimeError):
    pass


class DeviceUnavailable(ValueError):
    pass


class SyncForbidden(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _event_hash(event):
    # Immutable request identity survives removal of paths from the read projection.
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


class SQLStore:
    def __init__(self, session):
        self.db = session

    def close(self):
        self.db.close()

    def ready(self, production=False):
        self.db.execute(text("SELECT 1"))
        return not production or self.db.execute(text("SELECT version FROM schema_migrations WHERE version=:version"), {"version":REQUIRED_SCHEMA_VERSION}).first() is not None

    def _event_hash(self, event):
        if self.db.bind.dialect.name == 'postgresql':
            # Match the RPC/backfill canonicalization even when switching
            # between direct SQL and the REST store on the same PostgreSQL DB.
            return self.db.execute(text("SELECT encode(sha256(convert_to(CAST(CAST(:event AS jsonb) AS text),'UTF8')),'hex')"), {'event':json.dumps(event,ensure_ascii=False)}).scalar_one()
        return _event_hash(event)

    def _lock_owner(self, owner):
        self.db.execute(text("INSERT INTO sync_owner_locks(owner_id) VALUES (:owner) ON CONFLICT(owner_id) DO UPDATE SET owner_id=excluded.owner_id"), {'owner': owner})

    def device(self, owner, device):
        return self.db.scalar(select(Device).where(Device.owner_id == owner, Device.device_id == device).execution_options(populate_existing=True))

    def devices(self, owner):
        return self.db.scalars(select(Device).where(Device.owner_id == owner).order_by(Device.created_at)).all()

    def save_device(self, row):
        if row.id is None:
            self._add(row)
            return
        with self.db.no_autoflush:
            result = self.db.execute(update(Device).where(Device.owner_id == row.owner_id, Device.device_id == row.device_id, Device.revoked.is_(False), Device.origin == row.origin).values(display_name=row.display_name, last_seen_at=row.last_seen_at or utcnow()).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            raise DeviceUnavailable()
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
        self._lock_owner(owner)
        row = self.preferences(owner)
        invalidate_paths = not values['sync_paths'] and (row is None or row.sync_paths)
        row = row or SyncPreferences(owner_id=owner)
        for k, v in values.items():
            setattr(row, k, v)
        self.db.add(row)
        if not values['sync_agent_sessions']:
            from .graph_checkpoints import GraphCheckpoint, GraphPendingWrite
            for model in (GraphPendingWrite, GraphCheckpoint):
                self.db.execute(delete(model).where(model.owner_id == owner))
            from .local_step_store import LocalStep
            self.db.execute(update(LocalStep).where(LocalStep.owner_id == owner).values(response_json=None))
        if not values['sync_paths']:
            for event in self.db.scalars(select(SyncEvent).where(SyncEvent.owner_id == owner)).all():
                if event.request_hash is None:
                    event.request_hash = self._event_hash(event.body_json)
                invalidate_paths = invalidate_paths or any(k in event.body_json.get('payload', {}) for k in ('source', 'paths'))
                body = dict(event.body_json)
                body['payload'] = {k: v for k, v in body['payload'].items() if k not in {'source', 'paths'}}
                event.body_json = body
        if invalidate_paths:
            self.db.add(SyncInvalidation(owner_id=owner, kind='paths_redacted'))
        self.db.commit()

    def invalidations(self, owner, after=0, limit=100):
        return self.db.scalars(select(SyncInvalidation).where(SyncInvalidation.owner_id == owner, SyncInvalidation.id > after).order_by(SyncInvalidation.id).limit(limit)).all()

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
        # Low-level fixture support. HTTP ingest uses the atomic batch below.
        self._lock_owner(row.owner_id)
        row.request_hash = self._event_hash(row.body_json)
        self._add(row)

    def ingest_batch(self, owner, device, events):
        accepted, duplicate = [], []
        try:
            self._lock_owner(owner)
            registered = self.db.scalar(select(Device).where(Device.owner_id == owner, Device.device_id == device).with_for_update(read=True).execution_options(populate_existing=True))
            if registered is None or registered.revoked:
                raise DeviceUnavailable()
            prefs = self.preferences(owner)
            for event in events:
                existing = self.existing_event(owner, event['eventId'], device, event['sequence'])
                if existing:
                    if existing.event_id != event['eventId'] or (existing.request_hash != self._event_hash(event) if existing.request_hash else existing.body_json != event):
                        raise StoreConflict()
                    duplicate.append(event['eventId'])
                    continue
                if prefs and not prefs.sync_records:
                    raise SyncForbidden('RECORD_SYNC_DISABLED')
                if not (prefs and prefs.sync_paths) and any(k in event['payload'] for k in ('source', 'paths')):
                    raise SyncForbidden('PATH_SYNC_DISABLED')
                self.db.add(SyncEvent(owner_id=owner, device_id=device, event_id=event['eventId'], sequence=event['sequence'], body_json=event, request_hash=self._event_hash(event)))
                self.db.flush()
                accepted.append(event['eventId'])
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise StoreConflict() from None
        except Exception:
            self.db.rollback()
            raise
        return {'acceptedEventIds': accepted, 'duplicateEventIds': duplicate, 'rejected': []}

    def turns(self, owner, session, after=0, limit=100):
        return self.db.scalars(select(AgentTurn).where(AgentTurn.owner_id == owner, AgentTurn.session_id == session, AgentTurn.id > after).order_by(AgentTurn.id).limit(limit)).all()

    def deleted_sessions(self, owner, after=0, limit=100):
        return self.db.scalars(select(DeletedAgentSession).where(DeletedAgentSession.owner_id == owner, DeletedAgentSession.id > after).order_by(DeletedAgentSession.id).limit(limit)).all()

    def delete_session(self, owner, device, session):
        try:
            self._lock_owner(owner)
            registered = self.db.scalar(select(Device).where(Device.owner_id == owner, Device.device_id == device).with_for_update(read=True).execution_options(populate_existing=True))
            if registered is None or registered.revoked:
                raise DeviceUnavailable()
            if self.db.scalar(select(DeletedAgentSession).where(DeletedAgentSession.owner_id == owner, DeletedAgentSession.session_id == session)) is None:
                self.db.add(DeletedAgentSession(owner_id=owner, session_id=session))
            from .graph_checkpoints import GraphCheckpoint, GraphPendingWrite
            for checkpoint_model in (GraphPendingWrite, GraphCheckpoint):
                self.db.execute(delete(checkpoint_model).where(checkpoint_model.owner_id == owner, checkpoint_model.session_id == session))
            from .local_step_store import LocalStep
            self.db.execute(update(LocalStep).where(LocalStep.owner_id == owner, LocalStep.session_id == session).values(response_json=None))
            self.db.execute(delete(AgentTurn).where(AgentTurn.owner_id == owner, AgentTurn.session_id == session))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def reserve_model(self, owner, device, session, turn, query):
        try:
            self._lock_owner(owner)
            registered=self.db.scalar(select(Device).where(Device.owner_id==owner,Device.device_id==device).with_for_update(read=True).execution_options(populate_existing=True))
            if registered is None or registered.revoked:raise DeviceUnavailable()
            if self.db.scalar(select(DeletedAgentSession).where(DeletedAgentSession.owner_id==owner,DeletedAgentSession.session_id==session)) is not None:raise SessionDeleted()
            request_hash=hashlib.sha256(query.encode('utf-8')).hexdigest()
            prior=self.db.get(AgentModelRequest,(owner,session,turn))
            history=self.db.scalar(select(AgentTurn).where(AgentTurn.owner_id==owner,AgentTurn.session_id==session,AgentTurn.turn_id==turn))
            if prior is not None:
                if prior.device_id!=device or prior.request_hash!=request_hash:raise StoreConflict()
                if prior.state=='completed' and history is not None:
                    result={'state':'completed','response':history.response_json,'persisted':True,'duplicate':True}
                else:
                    result={'state':prior.state}
                self.db.commit()
                return result
            if history is not None:raise StoreConflict()
            attempt=str(uuid4())
            self.db.add(AgentModelRequest(owner_id=owner,session_id=session,turn_id=turn,device_id=device,request_hash=request_hash,attempt_id=attempt,state='pending'))
            self.db.commit()
            return {'state':'reserved','attemptId':attempt}
        except Exception:
            self.db.rollback()
            raise

    def finish_model(self, owner, device, session, turn, attempt, query, response):
        try:
            self._lock_owner(owner)
            row=self.db.get(AgentModelRequest,(owner,session,turn))
            if row is None or row.device_id!=device or row.attempt_id!=attempt or row.state!='pending':raise StoreConflict()
            if response is None:
                row.state='uncertain'
                self.db.commit()
                return {'state':'uncertain'}
            if self.db.scalar(select(DeletedAgentSession).where(DeletedAgentSession.owner_id==owner,DeletedAgentSession.session_id==session)) is not None:
                # Match RPC transaction rollback: preserve the reservation
                # when delivery is rejected; never re-dispatch it.
                raise SessionDeleted()
            registered=self.db.scalar(select(Device).where(Device.owner_id==owner,Device.device_id==device).with_for_update(read=True).execution_options(populate_existing=True))
            if registered is None or registered.revoked:
                raise DeviceUnavailable()
            if row.request_hash!=hashlib.sha256(query.encode('utf-8')).hexdigest():raise StoreConflict()
            pref=self.preferences(owner)
            persisted=pref is None or pref.sync_agent_sessions
            if persisted:
                self.db.add(AgentTurn(owner_id=owner,session_id=session,turn_id=turn,device_id=device,query=query,response_json=response))
            row.state='completed' if persisted else 'completed_unretained'
            self.db.commit()
            return {'state':row.state,'response':response,'persisted':persisted,'duplicate':False}
        except Exception:
            self.db.rollback()
            raise

    def save_turn(self, owner, device, session, turn, query, response):
        try:
            self._lock_owner(owner)
            registered = self.db.scalar(select(Device).where(Device.owner_id == owner, Device.device_id == device).with_for_update(read=True).execution_options(populate_existing=True))
            if registered is None or registered.revoked:
                raise DeviceUnavailable()
            if self.db.scalar(select(DeletedAgentSession).where(DeletedAgentSession.owner_id == owner, DeletedAgentSession.session_id == session)) is not None:
                raise SessionDeleted()
            if self.db.get(AgentModelRequest,(owner,session,turn)) is not None:raise StoreConflict()
            prior = self.db.scalar(select(AgentTurn).where(AgentTurn.owner_id == owner, AgentTurn.session_id == session, AgentTurn.turn_id == turn))
            if prior is not None:
                if prior.device_id != device or prior.query != query:
                    raise StoreConflict()
                self.db.commit()
                return {"response": prior.response_json, "persisted": True, "duplicate": True}
            prefs = self.preferences(owner)
            if prefs is not None and not prefs.sync_agent_sessions:
                self.db.commit()
                return {"response": response, "persisted": False, "duplicate": False}
            self.db.add(AgentTurn(owner_id=owner,device_id=device,session_id=session,turn_id=turn,query=query,response_json=response))
            self.db.commit()
            return {"response": response, "persisted": True, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def action(self, owner, action_id):
        return self.db.scalar(select(AgentAction).where(AgentAction.owner_id == owner, AgentAction.id == action_id))

    def add_action(self, row):
        from .action_commit import commit_action
        commit_action(self, row)

    def transition_action(self, row, new_state, result_code=None):
        updated_at = utcnow()
        result = self.db.execute(update(AgentAction).where(AgentAction.owner_id == row.owner_id, AgentAction.id == row.id, AgentAction.state == row.state).values(state=new_state, result_code=result_code, state_updated_at=updated_at).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            raise StoreConflict()
        self.db.commit()
        row.state = new_state
        row.result_code = result_code
        row.state_updated_at = updated_at


def _literal(value):
    # Standalone eq filters consume the whole value. urlencode escapes query
    # separators; adding grammar quotes would compare against literal quotes.
    return str(value)


def _row(model, values):
    values = dict(values)
    for name in ('created_at', 'last_seen_at', 'expires_at', 'updated_at', 'state_updated_at', 'deleted_at'):
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
            status = exc.code
            try:
                problem = json.loads(exc.read())
            except (ValueError, OSError):
                problem = {}
            exc.close()
            if status == 403 and problem.get('message') in {'RECORD_SYNC_DISABLED', 'PATH_SYNC_DISABLED'}:
                raise SyncForbidden(problem['message']) from None
            if status == 403 and problem.get('message') == 'DEVICE_REVOKED_OR_UNREGISTERED':
                raise DeviceUnavailable() from None
            if status == 410 and problem.get('message') == 'SESSION_DELETED':
                raise SessionDeleted() from None
            if status == 409:
                raise StoreConflict() from None
            raise StoreUnavailable('CloudBase database request failed') from None
        except (URLError, TimeoutError, ValueError):
            raise StoreUnavailable('CloudBase database unavailable') from None

    def ready(self, production=False):
        return bool(self.request('schema_migrations', query={'select': 'version', 'version': 'eq.'+str(REQUIRED_SCHEMA_VERSION), 'limit': '1'}))

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
        rows = self.request('devices', 'PATCH', self._filters(row.owner_id, device_id=row.device_id), {'revoked': True})
        if len(rows) != 1:
            raise DeviceUnavailable()
        row.revoked = True

    def preferences(self, owner):
        rows = self.request('sync_preferences', query={**self._filters(owner), 'limit': 1})
        return _row(SyncPreferences, rows[0]) if rows else None

    def set_preferences(self, owner, values):
        self.request('rpc/yingxu_set_preferences', 'POST', body={'p_owner': owner, **{'p_' + k: v for k, v in values.items()}})

    def invalidations(self, owner, after=0, limit=100):
        rows = self.request('sync_invalidations', query={**self._filters(owner),'id':'gt.'+str(after),'order':'id.asc','limit':limit})
        return [_row(SyncInvalidation,row) for row in rows]

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

    def ingest_batch(self, owner, device, events):
        return self.request('rpc/yingxu_ingest_batch', 'POST', body={'p_owner': owner, 'p_device': device, 'p_events': events})

    def turns(self, owner, session, after=0, limit=100):
        rows = self.request('agent_turns', query={**self._filters(owner, session_id=session), 'id':'gt.'+str(after), 'order':'id.asc', 'limit':limit})
        return [_row(AgentTurn, row) for row in rows]

    def deleted_sessions(self, owner, after=0, limit=100):
        rows = self.request('deleted_agent_sessions', query={**self._filters(owner),'id':'gt.'+str(after),'order':'id.asc','limit':limit})
        return [_row(DeletedAgentSession,row) for row in rows]

    def delete_session(self, owner, device, session):
        self.request('rpc/yingxu_delete_session', 'POST', body={'p_owner':owner,'p_device':device,'p_session':session})

    def reserve_model(self, owner, device, session, turn, query):
        return self.request('rpc/yingxu_reserve_model', 'POST', body={'p_owner':owner,'p_device':device,'p_session':session,'p_turn':turn,'p_query':query,'p_attempt':str(uuid4())})

    def finish_model(self, owner, device, session, turn, attempt, query, response):
        return self.request('rpc/yingxu_finish_model', 'POST', body={'p_owner':owner,'p_device':device,'p_session':session,'p_turn':turn,'p_attempt':attempt,'p_query':query,'p_response':response})

    def save_turn(self, owner, device, session, turn, query, response):
        return self.request('rpc/yingxu_save_turn', 'POST', body={'p_owner':owner,'p_device':device,'p_session':session,'p_turn':turn,'p_query':query,'p_response':response})

    def action(self, owner, action_id):
        rows = self.request('agent_actions', query={**self._filters(owner, id=action_id), 'limit': 1})
        return _row(AgentAction, rows[0]) if rows else None

    def add_action(self, row):
        from .action_commit import commit_action
        commit_action(self, row)

    def transition_action(self, row, new_state, result_code=None):
        updated_at = utcnow()
        rows = self.request('agent_actions', 'PATCH', self._filters(row.owner_id, id=row.id, state=row.state), {'state': new_state, 'result_code': result_code, 'state_updated_at': updated_at})
        if len(rows) != 1:
            raise StoreConflict()
        row.state = new_state
        row.result_code = result_code
        row.state_updated_at = updated_at
