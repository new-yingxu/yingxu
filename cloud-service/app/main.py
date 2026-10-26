from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import Principal, require_principal
from .config import Settings
from .db import Base, build_database
from .models import AgentAction, Device, SyncEvent, utcnow
from .store import SQLStore, PostgRESTStore, StoreConflict, StoreUnavailable, DeviceUnavailable

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
CONTRACTS_DIR = SERVICE_ROOT / "contracts"
if not CONTRACTS_DIR.is_dir():
    CONTRACTS_DIR = REPO_ROOT / "contracts"
SYNC_SCHEMA = json.loads((CONTRACTS_DIR / "sync-event-v1.schema.json").read_text())
PROPOSAL_SCHEMA = json.loads((CONTRACTS_DIR / "agent-proposal-v1.schema.json").read_text())
SYNC_VALIDATOR = Draft202012Validator(SYNC_SCHEMA, format_checker=FormatChecker())
PROPOSAL_VALIDATOR = Draft202012Validator(PROPOSAL_SCHEMA, format_checker=FormatChecker())
WRITE_TOOLS = {"preview_rename", "rename_item", "preview_move", "move_item", "upload_baidu", "upload_quark"}
READ_TOOLS = {"search_media", "get_backup_job"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    errors = settings.configuration_errors()
    engine, session_factory = build_database(settings)
    if engine is not None and settings.app_env == "development":
        Base.metadata.create_all(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.config_errors = errors
    app.state.logger = logging.getLogger("yingxu.cloud")
    yield
    if engine is not None:
        engine.dispose()


app = FastAPI(title="影序 Cloud Service", version="0.1.0", lifespan=lifespan)
_cors_settings = Settings.from_env()
_cors_origins = [
    origin for origin in _cors_settings.cors_origins
    if "*" not in origin and (_cors_settings.app_env != "production" or origin.startswith("https://"))
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@contextmanager
def open_store(app):
    settings = app.state.settings
    if app.state.session_factory is not None:
        store = SQLStore(app.state.session_factory())
    elif settings.cloudbase_env_id and settings.cloudbase_api_key:
        store = PostgRESTStore(settings.cloudbase_env_id, settings.cloudbase_api_key)
    else:
        raise StoreUnavailable("database not configured")
    try:
        yield store
    finally:
        store.close()


def get_db(request: Request):
    with open_store(request.app) as store:
        yield store


@app.exception_handler(StoreUnavailable)
async def database_unavailable(request: Request, exc: StoreUnavailable):
    return JSONResponse(status_code=503, content={"detail": {"code": "DATABASE_UNAVAILABLE"}})


@app.exception_handler(StoreConflict)
async def persistence_conflict(request: Request, exc: StoreConflict):
    return JSONResponse(status_code=409, content={"detail": {"code": "PERSISTENCE_CONFLICT"}})


@app.exception_handler(DeviceUnavailable)
async def device_unavailable(request: Request, exc: DeviceUnavailable):
    return JSONResponse(status_code=403, content={"detail": {"code": "DEVICE_REVOKED_OR_UNREGISTERED"}})


def _schema_error(validator, data: dict[str, Any]) -> str | None:
    error = next(iter(validator.iter_errors(data)), None)
    return None if error is None else f"{list(error.absolute_path)}: {error.message}"


def _device(db: Any, principal: Principal) -> Device:
    device = db.device(principal.owner_id, principal.device_id)
    if device is None or device.revoked or device.origin != principal.origin:
        raise HTTPException(status_code=403, detail={"code": "DEVICE_REVOKED_OR_UNREGISTERED"})
    db.touch_device(device)
    return device


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)


class SyncBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action_id: str = Field(default_factory=lambda: str(uuid4()), alias="actionId", min_length=1, max_length=500)
    tool: Literal[
        "search_media", "get_backup_job", "preview_rename", "rename_item",
        "preview_move", "move_item", "upload_baidu", "upload_quark",
    ]
    file_ids: list[str] = Field(default_factory=list, alias="fileIds", max_length=1000)
    destination_grant_id: str | None = Field(default=None, alias="destinationGrantId", min_length=1, max_length=500)
    requested_name: str | None = Field(default=None, alias="requestedName", min_length=1, max_length=180)
    query: str | None = Field(default=None, max_length=500)
    job_id: str | None = Field(default=None, alias="jobId", min_length=1, max_length=500)
    expires_at: datetime = Field(alias="expiresAt")

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class SyncPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sync_records: bool = Field(alias="syncRecords")
    sync_agent_sessions: bool = Field(alias="syncAgentSessions")
    sync_thumbnails: bool = Field(alias="syncThumbnails")
    sync_paths: bool = Field(alias="syncPaths")


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)


class ActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    state: Literal["queued", "running", "succeeded", "failed", "declined", "expired"]
    result_code: str | None = Field(default=None, alias="resultCode", max_length=100)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@app.get("/healthz")
def healthz(request: Request):
    return {"ok": True, "service": "yingxu-cloud", "version": request.app.state.settings.app_version}


@app.get("/readyz")
def readyz(request: Request):
    settings: Settings = request.app.state.settings
    try:
        with open_store(request.app) as store:
            db_ready = store.ready(production=settings.app_env == "production")
    except Exception:
        db_ready = False
    auth_ready = (
        settings.auth_mode == "jwt"
        and bool(settings.jwt_issuer and settings.jwt_audience and settings.jwt_jwks_url)
    ) or (
        settings.auth_mode == "dev"
        and settings.app_env == "development"
        and bool(settings.dev_auth_token)
    )
    ready = db_ready and auth_ready and not request.app.state.config_errors
    return JSONResponse(status_code=200 if ready else 503, content={
        "ready": ready,
        "database": "ready" if db_ready else "unavailable",
        "authentication": "configured" if auth_ready else "not_configured",
        "configurationErrors": request.app.state.config_errors,
        "version": settings.app_version,
    })


@app.post("/v1/devices/register")
def register_device(
    request: Request,
    payload: DeviceRegistration,
    principal: Principal = Depends(require_principal),
    db: Any = Depends(get_db),
):
    device = db.device(principal.owner_id, principal.device_id)
    if device is None:
        device = Device(owner_id=principal.owner_id, device_id=principal.device_id, origin=principal.origin, display_name=payload.display_name)
    elif device.revoked or device.origin != principal.origin:
        raise HTTPException(status_code=403, detail={"code": "DEVICE_REVOKED_OR_ORIGIN_MISMATCH"})
    device.last_seen_at = utcnow()
    device.display_name = payload.display_name
    db.save_device(device)
    return {"deviceId": device.device_id, "displayName": device.display_name, "origin": device.origin, "revoked": False}


@app.get("/v1/devices")
def list_devices(principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    devices = db.devices(principal.owner_id)
    return [{"deviceId": d.device_id, "displayName": d.display_name, "origin": d.origin, "revoked": d.revoked, "lastSeenAt": d.last_seen_at} for d in devices]


@app.post("/v1/devices/{device_id}/revoke")
def revoke_device(device_id: str, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if device_id == principal.device_id:
        raise HTTPException(status_code=409, detail={"code": "CANNOT_REVOKE_CURRENT_DEVICE"})
    device = db.device(principal.owner_id, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail={"code": "DEVICE_NOT_FOUND"})
    db.revoke_device(device)
    return {"deviceId": device_id, "revoked": True}


@app.post("/v1/sync/events")
def ingest_events(batch: SyncBatch, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    accepted: list[str] = []
    duplicate: list[str] = []
    for event in batch.events:
        error = _schema_error(SYNC_VALIDATOR, event)
        if error:
            raise HTTPException(status_code=422, detail={"code": "INVALID_SYNC_EVENT", "reason": error})
        if event["deviceId"] != principal.device_id:
            raise HTTPException(status_code=403, detail={"code": "DEVICE_MISMATCH"})
        payload = event["payload"]
        # Paths are accepted only when that owner explicitly enabled path sync.
        prefs = db.preferences(principal.owner_id)
        if prefs and not prefs.sync_records:
            raise HTTPException(status_code=403, detail={"code": "RECORD_SYNC_DISABLED"})
        if not (prefs and prefs.sync_paths) and ("source" in payload or "paths" in payload):
            raise HTTPException(status_code=403, detail={"code": "PATH_SYNC_DISABLED"})
        event_id = event["eventId"]
        existing = db.existing_event(principal.owner_id, event_id, principal.device_id, event["sequence"])
        if existing:
            if existing.event_id != event_id or existing.body_json != event:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "eventId": event_id})
            duplicate.append(event_id)
            continue
        row = SyncEvent(
            owner_id=principal.owner_id,
            device_id=principal.device_id,
            event_id=event_id,
            sequence=event["sequence"],
            body_json=event,
        )
        try:
            db.add_event(row)
        except StoreConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "eventId": event_id}) from exc
        accepted.append(event_id)
    return {"acceptedEventIds": accepted, "duplicateEventIds": duplicate, "rejected": []}


@app.get("/v1/sync/snapshot")
def sync_snapshot(limit: int = 100, after_id: int = 0, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    limit = min(max(limit, 1), 500)
    events = db.events(principal.owner_id, after=max(after_id, 0), limit=limit + 1)
    has_more = len(events) > limit
    events = events[:limit]
    return {"events": [e.body_json for e in events], "nextCursor": events[-1].id if events else after_id, "hasMore": has_more}


@app.put("/v1/account/sync-preferences")
def update_sync_preferences(payload: SyncPreferencesUpdate, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    db.set_preferences(principal.owner_id, payload.model_dump())
    return payload.model_dump(by_alias=True)


@app.post("/v1/agent/actions")
def propose_action(payload: ProposalRequest, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if principal.origin == "mobile" and payload.tool not in READ_TOOLS:
        raise HTTPException(status_code=403, detail={"code": "MOBILE_DESKTOP_CONTROL_FORBIDDEN"})
    proposal = {
        "schemaVersion": 1,
        "actionId": payload.action_id,
        "origin": principal.origin,
        "tool": payload.tool,
        "fileIds": payload.file_ids,
        "expiresAt": _as_utc(payload.expires_at).isoformat().replace("+00:00", "Z"),
    }
    optional = {
        "destinationGrantId": payload.destination_grant_id,
        "requestedName": payload.requested_name,
        "query": payload.query,
        "jobId": payload.job_id,
    }
    proposal.update({k: v for k, v in optional.items() if v is not None})
    error = _schema_error(PROPOSAL_VALIDATOR, proposal)
    if error:
        raise HTTPException(status_code=422, detail={"code": "INVALID_AGENT_PROPOSAL", "reason": error})
    if payload.expires_at <= utcnow():
        raise HTTPException(status_code=422, detail={"code": "PROPOSAL_ALREADY_EXPIRED"})
    if payload.tool in WRITE_TOOLS:
        state = "awaiting_local_confirmation"
    else:
        state = "proposed"
    action = AgentAction(
        id=payload.action_id,
        owner_id=principal.owner_id,
        device_id=principal.device_id,
        origin=principal.origin,
        tool=payload.tool,
        state=state,
        proposal_json=proposal,
        expires_at=_as_utc(payload.expires_at),
    )
    try:
        db.add_action(action)
    except StoreConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "ACTION_ID_CONFLICT"}) from exc
    return {"proposal": proposal, "state": state, "executionAuthorized": False}


@app.post("/v1/agent/turns")
def agent_turn(payload: AgentTurnRequest, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    query = payload.query
    if not query.strip():
        raise HTTPException(status_code=422, detail={"code": "INVALID_QUERY"})
    # Initial release uses only local searchable sync summaries; no model call is claimed here.
    rows = db.events(principal.owner_id, limit=200, newest=True)
    term = query.casefold()
    matches = []
    for row in rows:
        payload = row.body_json.get("payload", {})
        haystack = f"{payload.get('name', '')} {payload.get('jobId', '')} {payload.get('state', '')}".casefold()
        if term in haystack:
            matches.append({
                "jobId": payload.get("jobId"),
                "name": payload.get("name"),
                "state": payload.get("state"),
                "occurredAt": row.body_json.get("occurredAt"),
                "source": "synced_job_snapshot",
            })
    return {"answer": f"找到 {len(matches)} 条已同步的任务记录。" if matches else "已同步资料中没有匹配的任务记录。", "matches": matches[:50], "model": None, "dataSource": "owner_scoped_sync_events"}


@app.post("/v1/agent/actions/{action_id}/receipt")
def action_receipt(action_id: str, receipt: ActionReceipt, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if principal.origin != "desktop":
        raise HTTPException(status_code=403, detail={"code": "DESKTOP_RECEIPT_REQUIRED"})
    action = db.action(principal.owner_id, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail={"code": "ACTION_NOT_FOUND"})
    if action.device_id != principal.device_id:
        raise HTTPException(status_code=403, detail={"code": "ACTION_DEVICE_MISMATCH"})
    allowed = {
        "awaiting_local_confirmation": {"queued", "declined", "expired"},
        "queued": {"running", "failed"},
        "running": {"succeeded", "failed"},
        "proposed": {"declined", "expired"},
    }
    if receipt.state not in allowed.get(action.state, set()):
        raise HTTPException(status_code=409, detail={"code": "INVALID_ACTION_TRANSITION", "state": action.state})
    if _as_utc(action.expires_at) <= utcnow() and receipt.state in {"queued", "running", "succeeded"}:
        db.transition_action(action, "expired")
        raise HTTPException(status_code=409, detail={"code": "PROPOSAL_EXPIRED"})
    db.transition_action(action, receipt.state)
    return {"actionId": action_id, "state": action.state, "resultCode": receipt.result_code, "executionAuthorized": False}


@app.websocket("/v1/ws")
async def sync_socket(websocket: WebSocket):
    import asyncio

    await websocket.accept()
    await websocket.send_json({"type": "auth.required"})
    try:
        message = await websocket.receive_json()
        token = message.get("token") if isinstance(message, dict) else None
        settings: Settings = websocket.app.state.settings
        if not isinstance(token, str) or not token:
            await websocket.close(code=4401, reason="AUTH_REQUIRED")
            return
        from .auth import principal_from_token
        try:
            principal = principal_from_token(token, settings)
        except Exception:
            await websocket.close(code=4403, reason="AUTH_PROVIDER_NOT_BOUND_FOR_WEBSOCKET")
            return
        with open_store(websocket.app) as db:
            device = db.device(principal.owner_id, principal.device_id)
            if device is None or device.revoked or device.origin != principal.origin:
                await websocket.close(code=4403, reason="DEVICE_REVOKED_OR_UNREGISTERED")
                return
            is_resume = "afterCursor" in message
            cursor = message.get("afterCursor", 0)
            if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
                await websocket.close(code=4400, reason="INVALID_CURSOR")
                return
            while True:
                events = db.events(principal.owner_id, after=cursor, limit=100)
                for event in events:
                    await websocket.send_json({"type": "sync.event", "event": event.body_json, "cursor": event.id})
                    cursor = event.id
                if len(events) < 100:
                    break
            await websocket.send_json({
                "type": "sync.resume.complete" if is_resume else "sync.snapshot.complete",
                "cursor": cursor,
            })
        while True:
            try:
                incoming = await asyncio.wait_for(websocket.receive_json(), timeout=2)
                if isinstance(incoming, dict) and incoming.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "cursor": cursor})
            except asyncio.TimeoutError:
                pass
            try:
                principal_from_token(token, settings)
            except Exception:
                await websocket.close(code=4401, reason="SESSION_EXPIRED_OR_INVALID")
                return
            with open_store(websocket.app) as db:
                device = db.device(principal.owner_id, principal.device_id)
                if device is None or device.revoked or device.origin != principal.origin:
                    await websocket.close(code=4403, reason="DEVICE_REVOKED")
                    return
                new_events = db.events(principal.owner_id, after=cursor, limit=100)
                for event in new_events:
                    await websocket.send_json({"type": "sync.event", "event": event.body_json, "cursor": event.id})
                    cursor = event.id
    except StoreUnavailable:
        await websocket.close(code=1013, reason="DATABASE_UNAVAILABLE")
    except (WebSocketDisconnect, ValueError, TypeError):
        return
