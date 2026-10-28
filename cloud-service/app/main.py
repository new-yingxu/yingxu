from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import hashlib
import logging
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Path as PathParam
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

from .auth import Principal, require_principal, bearer
from .agent_tools import ReadOnlyToolRegistry,ToolNotAllowed,tool_catalog
from .agent_graph import run_turn_graph, build_turn_graph
from .graph_checkpoints import PrincipalCheckpointSaver
from .sync_snapshots import SnapshotStore, SnapshotNotFound, SnapshotExpired, SnapshotLimit
from .account_data import AccountDataStore
from .preset_store import PresetStore, PresetBody, PresetConflict, PresetNotFound, PresetLimit, PresetSyncDisabled
from .config import Settings, production_cors_origin_allowed
from .model_provider import credential_for_owner, generate_answer, ModelNotConfigured, ModelUnavailable
from .db import Base, build_database
from .models import AgentAction, Device, SyncEvent, utcnow
from .store import SQLStore, PostgRESTStore, StoreConflict, StoreUnavailable, DeviceUnavailable, SyncForbidden, SessionDeleted

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
MAX_SYNC_CURSOR = 2**63 - 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    errors = settings.configuration_errors()
    engine, session_factory = build_database(settings)
    if engine is not None and settings.app_env == "development":
        from . import graph_checkpoints  # register checkpoint models before local table creation
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
    if "*" not in origin and (_cors_settings.app_env != "production" or production_cors_origin_allowed(origin))
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Yingxu-Session"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/v1/auth/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
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


@app.exception_handler(SnapshotNotFound)
async def snapshot_not_found(request: Request, exc: SnapshotNotFound):
    return JSONResponse(status_code=404,content={"detail":{"code":"SNAPSHOT_NOT_FOUND"}})


@app.exception_handler(SnapshotExpired)
async def snapshot_expired(request: Request, exc: SnapshotExpired):
    return JSONResponse(status_code=410,content={"detail":{"code":"SNAPSHOT_EXPIRED"}})


@app.exception_handler(SnapshotLimit)
async def snapshot_limit(request: Request, exc: SnapshotLimit):
    return JSONResponse(status_code=429,content={"detail":{"code":"SNAPSHOT_LIMIT_REACHED"}})


@app.exception_handler(PresetConflict)
async def preset_conflict(request: Request, exc: PresetConflict):
    return JSONResponse(status_code=409,content={"detail":{"code":"PRESET_VERSION_CONFLICT"}})


@app.exception_handler(PresetNotFound)
async def preset_not_found(request: Request, exc: PresetNotFound):
    return JSONResponse(status_code=404,content={"detail":{"code":"PRESET_NOT_FOUND"}})


@app.exception_handler(PresetLimit)
async def preset_limit(request: Request, exc: PresetLimit):
    return JSONResponse(status_code=409,content={"detail":{"code":"PRESET_LIMIT_REACHED"}})


@app.exception_handler(PresetSyncDisabled)
async def preset_disabled(request: Request, exc: PresetSyncDisabled):
    return JSONResponse(status_code=403,content={"detail":{"code":"SYNC_RECORDS_DISABLED"}})


@app.exception_handler(SessionDeleted)
async def session_deleted(request: Request, exc: SessionDeleted):
    return JSONResponse(status_code=410,content={"detail":{"code":"SESSION_DELETED"}})


@app.exception_handler(SyncForbidden)
async def sync_forbidden(request: Request, exc: SyncForbidden):
    return JSONResponse(status_code=403, content={"detail": {"code": exc.code}})


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


class SessionRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    use_model: bool = Field(default=False, alias="useModel")
    resume: bool = False
    session_id: str | None = Field(default=None, alias="sessionId", min_length=1, max_length=160)
    turn_id: str | None = Field(default=None, alias="turnId", min_length=1, max_length=160)


class SnapshotStart(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)


class SyncDataCleanup(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    request_id: str = Field(alias="requestId",min_length=1,max_length=160)
    confirmed: bool


class PresetWrite(PresetBody):
    expected_version: int = Field(alias="expectedVersion",ge=0,le=2**31-1,strict=True)


class PresetDeletion(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    expected_version: int = Field(alias="expectedVersion",ge=1,le=2**31-1,strict=True)
    confirmed: bool


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str,Any]


class SessionDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class ActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    state: Literal["queued", "running", "succeeded", "failed", "declined", "expired", "cancelled"]
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
    auth_ready = settings.authentication_configured()
    ready = db_ready and auth_ready and not request.app.state.config_errors
    return JSONResponse(status_code=200 if ready else 503, content={
        "ready": ready,
        "database": "ready" if db_ready else "unavailable",
        "authentication": "configured" if auth_ready else "not_configured",
        "configurationErrors": request.app.state.config_errors,
        "version": settings.app_version,
    })


@app.post("/v1/auth/session/exchange")
def exchange_account_session(payload: DeviceRegistration, request: Request, credentials: Any = Depends(bearer), db: Any = Depends(get_db)):
    settings=request.app.state.settings
    if settings.auth_mode!='cloudbase' or settings.configuration_errors():
        raise HTTPException(status_code=503,detail={"code":"AUTH_EXCHANGE_NOT_CONFIGURED"})
    if credentials is None:
        raise HTTPException(status_code=401,detail={"code":"AUTH_REQUIRED"})
    from .cloudbase_identity import verify_account_channel,InvalidCloudBaseIdentity,CloudBaseIdentityUnavailable
    from .session_tokens import issue_device_session
    try:
        account=verify_account_channel(settings.cloudbase_env_id,credentials.credentials,json.loads(settings.cloudbase_client_origins))
    except InvalidCloudBaseIdentity:
        raise HTTPException(status_code=401,detail={"code":"INVALID_ACCOUNT_SESSION"}) from None
    except CloudBaseIdentityUnavailable:
        raise HTTPException(status_code=503,detail={"code":"IDENTITY_PROVIDER_UNAVAILABLE"}) from None
    # Device identity is generated here, never copied from a header/body/profile.
    device_id='device-'+uuid4().hex
    token=issue_device_session(settings.session_private_key,settings.session_key_id,settings.jwt_issuer,settings.jwt_audience,
        owner_id=account.owner_id,device_id=device_id,origin=account.origin)
    db.save_device(Device(owner_id=account.owner_id,device_id=device_id,origin=account.origin,display_name=payload.display_name))
    return {"accessToken":token,"tokenType":"Bearer","expiresIn":900,"ownerId":account.owner_id,
            "deviceId":device_id,"origin":account.origin,"deviceAttested":False}


@app.post("/v1/auth/session/renew")
def renew_account_session(payload: SessionRenewRequest, request: Request,
                          credentials: Any = Depends(bearer),
                          x_yingxu_session: str | None = Header(default=None,alias="X-Yingxu-Session")):
    """Explicit account reauthentication; this does not use a refresh_token."""
    settings=request.app.state.settings
    if settings.auth_mode!='cloudbase' or settings.configuration_errors():
        raise HTTPException(status_code=503,detail={"code":"AUTH_RENEW_NOT_CONFIGURED"})
    if credentials is None or not x_yingxu_session:
        raise HTTPException(status_code=401,detail={"code":"AUTH_REQUIRED"})
    from .cloudbase_identity import verify_account_channel,InvalidCloudBaseIdentity,CloudBaseIdentityUnavailable
    from .session_tokens import issue_device_session,verify_device_session,InvalidDeviceSession
    verification=json.loads(settings.session_verification_jwks) if settings.session_verification_jwks else None
    def verified_session():
        try:
            return verify_device_session(x_yingxu_session,settings.session_private_key,settings.session_key_id,
                settings.jwt_issuer,settings.jwt_audience,verification_jwks=verification)
        except InvalidDeviceSession:
            raise HTTPException(status_code=401,detail={"code":"INVALID_SESSION"}) from None
    session=verified_session()
    principal=Principal(session.owner_id,session.device_id,session.origin)
    # Each check has a fresh, closed store. Never hold a SQL connection while
    # contacting the account provider, and never create/register a new device.
    with open_store(request.app) as store:
        _device(store,principal)
    try:
        account=verify_account_channel(settings.cloudbase_env_id,credentials.credentials,json.loads(settings.cloudbase_client_origins))
    except InvalidCloudBaseIdentity:
        raise HTTPException(status_code=401,detail={"code":"INVALID_ACCOUNT_SESSION"}) from None
    except CloudBaseIdentityUnavailable:
        raise HTTPException(status_code=503,detail={"code":"IDENTITY_PROVIDER_UNAVAILABLE"}) from None
    if account.owner_id!=session.owner_id or account.origin!=session.origin:
        raise HTTPException(status_code=403,detail={"code":"ACCOUNT_SESSION_MISMATCH"})
    # Do not renew a token that expired while the provider was responding.
    verified_session()
    token=issue_device_session(settings.session_private_key,settings.session_key_id,settings.jwt_issuer,settings.jwt_audience,
        owner_id=session.owner_id,device_id=session.device_id,origin=session.origin)
    with open_store(request.app) as store:
        _device(store,principal)
    return {"accessToken":token,"tokenType":"Bearer","expiresIn":900,"ownerId":session.owner_id,
            "deviceId":session.device_id,"origin":session.origin,"deviceAttested":False}


@app.get("/.well-known/jwks.json")
def session_public_keys(request: Request):
    settings=request.app.state.settings
    if settings.auth_mode!='cloudbase' or settings.configuration_errors():
        raise HTTPException(status_code=503,detail={"code":"AUTH_EXCHANGE_NOT_CONFIGURED"})
    from .session_tokens import public_jwks
    return public_jwks(settings.session_private_key,settings.session_key_id,verification_jwks=json.loads(settings.session_verification_jwks) if settings.session_verification_jwks else None)


@app.post("/v1/auth/logout")
def logout_device(principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    # Logout revokes every app token bound to this registered device. A later
    # account exchange creates a fresh server-owned device id, not resurrection.
    device=db.device(principal.owner_id,principal.device_id)
    if device is None or device.origin!=principal.origin:
        raise HTTPException(status_code=403,detail={"code":"DEVICE_REVOKED_OR_UNREGISTERED"})
    if not device.revoked:db.revoke_device(device)
    return {"loggedOut":True,"deviceRevoked":True}


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
    for event in batch.events:
        error = _schema_error(SYNC_VALIDATOR, event)
        if error:
            raise HTTPException(status_code=422, detail={"code": "INVALID_SYNC_EVENT", "reason": error})
        if event["deviceId"] != principal.device_id:
            raise HTTPException(status_code=403, detail={"code": "DEVICE_MISMATCH"})
    try:
        return db.ingest_batch(principal.owner_id, principal.device_id, batch.events)
    except StoreConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "atomic": True}) from exc


@app.get("/v1/sync/invalidations")
def sync_invalidations(after_id: int = Query(default=0, ge=0, le=MAX_SYNC_CURSOR), principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    rows = db.invalidations(principal.owner_id, after=after_id, limit=101)
    page = rows[:100]
    return {"invalidations":[{"kind":r.kind,"createdAt":r.created_at} for r in page],
            "nextCursor":page[-1].id if page else after_id,"hasMore":len(rows)>100}


@app.post("/v1/sync/snapshots")
def begin_sync_snapshot(payload: SnapshotStart,principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    _device(db,principal)
    return SnapshotStore(db,principal).begin()


@app.get("/v1/sync/snapshots/{snapshot_id}")
def sync_snapshot_page(snapshot_id: str = PathParam(min_length=36,max_length=36),after_id: int = Query(default=0,ge=0,le=MAX_SYNC_CURSOR),limit: int = Query(default=100,ge=1,le=500),principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    _device(db,principal)
    try:
        return SnapshotStore(db,principal).page(snapshot_id,after_id,limit)
    except ValueError as exc:
        if isinstance(exc,(SnapshotNotFound,SnapshotExpired,SnapshotLimit,DeviceUnavailable)):raise
        raise HTTPException(status_code=422,detail={"code":"INVALID_SNAPSHOT_REQUEST"}) from None


@app.get("/v1/sync/snapshot")
def sync_snapshot(limit: int = 100, after_id: int = Query(default=0, ge=0, le=MAX_SYNC_CURSOR), principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    limit = min(max(limit, 1), 500)
    events = db.events(principal.owner_id, after=after_id, limit=limit + 1)
    has_more = len(events) > limit
    events = events[:limit]
    return {"events": [e.body_json for e in events], "nextCursor": events[-1].id if events else after_id, "hasMore": has_more}


@app.delete("/v1/account/sync-data")
def clear_sync_data(payload: SyncDataCleanup,principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    # The storage helper must check a matching receipt before requiring an active
    # device: successful cleanup revokes that device, but exact retries are safe.
    try:
        result=AccountDataStore(db,principal).clear(payload.request_id,payload.confirmed)
    except StoreConflict:
        raise HTTPException(status_code=409,detail={"code":"SYNC_DATA_REQUEST_ID_CONFLICT"}) from None
    except ValueError as exc:
        if isinstance(exc,DeviceUnavailable):raise
        raise HTTPException(status_code=422,detail={"code":"INVALID_SYNC_DATA_CONFIRMATION"}) from None
    return {**result,"accountDeleted":False,"originalsTouched":False,"reauthenticationRequired":True}


@app.get("/v1/account/sync-preferences", response_model=SyncPreferencesUpdate)
def get_sync_preferences(principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    row = db.preferences(principal.owner_id)
    defaults = {"sync_records": True, "sync_agent_sessions": True,
                "sync_thumbnails": False, "sync_paths": False}
    return {name: getattr(row, name) if row is not None else value
            for name, value in defaults.items()}


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
    existing = db.action(principal.owner_id, payload.action_id)
    if existing is not None:
        if existing.device_id != principal.device_id or existing.proposal_json != proposal:
            raise HTTPException(status_code=409, detail={"code": "ACTION_ID_CONFLICT"})
        return {"proposal": existing.proposal_json, "state": existing.state, "executionAuthorized": False, "duplicate": True}
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
        existing = db.action(principal.owner_id, payload.action_id)
        if existing and existing.device_id == principal.device_id and existing.proposal_json == proposal:
            return {"proposal": existing.proposal_json, "state": existing.state, "executionAuthorized": False, "duplicate": True}
        raise HTTPException(status_code=409, detail={"code": "ACTION_ID_CONFLICT"}) from exc
    return {"proposal": proposal, "state": state, "executionAuthorized": False, "duplicate": False}


@app.post("/v1/agent/tools/{tool_name}")
def invoke_agent_tool(tool_name: str, payload: ToolInvocation, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    try:
        result=ReadOnlyToolRegistry(db,principal).invoke(tool_name,payload.arguments)
    except ToolNotAllowed:
        raise HTTPException(status_code=403,detail={"code":"TOOL_NOT_ALLOWED"}) from None
    except (ValidationError,ValueError):
        raise HTTPException(status_code=422,detail={"code":"INVALID_TOOL_ARGUMENTS"}) from None
    return {"tool":tool_name,"result":result,"executionAuthorized":False}


@app.get("/v1/agent/tools")
def agent_tools(principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    return {"orchestrator":"langgraph","tools":tool_catalog(),"executionAuthorized":False}


@app.get("/v1/presets/changes")
def preset_changes(after_id: int = Query(default=0,ge=0,le=MAX_SYNC_CURSOR),principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    _device(db,principal)
    return PresetStore(db,principal).changes(after_id,100)


@app.put("/v1/presets/{preset_id}")
def put_preset(payload: PresetWrite,preset_id: str = PathParam(min_length=1,max_length=160),principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    _device(db,principal)
    try:
        return PresetStore(db,principal).put(preset_id,{k:getattr(payload,k) for k in ('name','pattern','isDefault')},payload.expected_version)
    except ValueError as exc:
        if isinstance(exc,(PresetConflict,PresetNotFound,PresetLimit,PresetSyncDisabled,DeviceUnavailable)):raise
        raise HTTPException(status_code=422,detail={"code":"INVALID_PRESET"}) from None


@app.delete("/v1/presets/{preset_id}")
def delete_preset(payload: PresetDeletion,preset_id: str = PathParam(min_length=1,max_length=160),principal: Principal = Depends(require_principal),db: Any = Depends(get_db)):
    _device(db,principal)
    try:
        return PresetStore(db,principal).delete(preset_id,payload.expected_version,payload.confirmed)
    except ValueError as exc:
        if isinstance(exc,(PresetConflict,PresetNotFound,PresetLimit,PresetSyncDisabled,DeviceUnavailable)):raise
        raise HTTPException(status_code=422,detail={"code":"INVALID_PRESET_DELETION"}) from None


@app.get("/v1/agent/model")
def agent_model_status(principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    try:
        credential=credential_for_owner(app.state.settings.model_owner_credentials,principal.owner_id,allow_local=app.state.settings.app_env=='development')
    except ModelNotConfigured:
        return {"configured":False,"requiresExplicitConsent":True,"executionAuthorized":False}
    return {"configured":True,"provider":credential.provider,"model":credential.model,"region":credential.region,
            "requiresExplicitConsent":True,"executionAuthorized":False}


@app.post("/v1/agent/turns")
def agent_turn(payload: AgentTurnRequest, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if (payload.session_id is None) != (payload.turn_id is None):
        raise HTTPException(status_code=422, detail={"code":"SESSION_AND_TURN_REQUIRED_TOGETHER"})
    query = payload.query
    if not query.strip():
        raise HTTPException(status_code=422, detail={"code": "INVALID_QUERY"})
    # Only read in the request-scoped store. Release its connection before the
    # graph starts; saver and nodes each obtain their own context-managed store.
    preferences=db.preferences(principal.owner_id)
    sync_enabled=preferences is None or preferences.sync_agent_sessions
    if isinstance(db,SQLStore):
        db.db.rollback()
    if payload.resume and payload.session_id is None:
        raise HTTPException(status_code=422,detail={"code":"RESUME_REQUIRES_SESSION_AND_TURN"})
    if payload.resume and not sync_enabled:
        raise HTTPException(status_code=409,detail={"code":"CHECKPOINT_SYNC_DISABLED"})
    saver=None
    thread_id=None
    if payload.session_id is not None and sync_enabled:
        identity=[principal.owner_id,principal.device_id,principal.origin,payload.session_id,payload.turn_id,query,payload.use_model]
        thread_id='turn-'+hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        saver=PrincipalCheckpointSaver(lambda:open_store(app),owner_id=principal.owner_id,device_id=principal.device_id,
            origin=principal.origin,session_id=payload.session_id,thread_id=thread_id)
    def retrieve():
        with open_store(app) as node_db:
            _device(node_db,principal)
            return ReadOnlyToolRegistry(node_db,principal).invoke('search_backup_jobs',{'query':query})
    def model_step(response):
        with open_store(app) as node_db:
            return model_step_owned(response,node_db)
    def model_step_owned(response,db):
        if payload.session_id is None:
            raise HTTPException(status_code=422,detail={"code":"MODEL_REQUIRES_SESSION_AND_TURN"})
        try:
            credential=credential_for_owner(app.state.settings.model_owner_credentials,principal.owner_id,allow_local=app.state.settings.app_env=='development')
        except ModelNotConfigured:
            raise HTTPException(status_code=503,detail={"code":"MODEL_NOT_CONFIGURED"}) from None
        try:
            reserved=db.reserve_model(principal.owner_id,principal.device_id,payload.session_id,payload.turn_id,query)
            if reserved['state']=='completed':
                saved=reserved
            elif reserved['state']!='reserved':
                raise HTTPException(status_code=409,detail={"code":"MODEL_REQUEST_"+reserved['state'].upper(),"automaticRetryAllowed":False})
            else:
                try:
                    def invoke_readonly(name,arguments):
                        with open_store(app) as tool_db:
                            _device(tool_db,principal)
                            return ReadOnlyToolRegistry(tool_db,principal).invoke(name,arguments)
                    generated=generate_answer(credential,query,response['matches'],invoke_tool=invoke_readonly)
                except ModelUnavailable:
                    db.finish_model(principal.owner_id,principal.device_id,payload.session_id,payload.turn_id,reserved['attemptId'],query,None)
                    raise HTTPException(status_code=502,detail={"code":"MODEL_OUTCOME_UNCERTAIN","automaticRetryAllowed":False}) from None
                response.update(generated)
                saved=db.finish_model(principal.owner_id,principal.device_id,payload.session_id,payload.turn_id,reserved['attemptId'],query,response)
            return {**saved['response'],"sessionId":payload.session_id,"turnId":payload.turn_id,"persisted":saved['persisted'],"duplicate":saved['duplicate']}
        except SessionDeleted:
            raise HTTPException(status_code=410,detail={"code":"SESSION_DELETED"}) from None
        except StoreConflict:
            raise HTTPException(status_code=409,detail={"code":"TURN_ID_CONFLICT"}) from None
    def persist_step(response):
        with open_store(app) as node_db:
            return persist_step_owned(response,node_db)
    def persist_step_owned(response,db):
        if payload.session_id is not None:
            try:
                saved = db.save_turn(principal.owner_id, principal.device_id, payload.session_id, payload.turn_id, query, response)
            except SessionDeleted:
                raise HTTPException(status_code=410, detail={"code":"SESSION_DELETED"}) from None
            except StoreConflict:
                raise HTTPException(status_code=409, detail={"code":"TURN_ID_CONFLICT"}) from None
            return {**saved['response'], "sessionId":payload.session_id,"turnId":payload.turn_id,
                    "persisted":saved['persisted'],"duplicate":saved['duplicate']}
        return response
    if payload.resume:
        graph=build_turn_graph(retrieve,model_step,persist_step,checkpointer=saver)
        config={'configurable':{'thread_id':thread_id}}
        checkpoint=saver.get_tuple(config)
        if checkpoint is None:
            raise HTTPException(status_code=409,detail={"code":"CHECKPOINT_NOT_FOUND"})
        state=graph.get_state(config)
        if not state.next:
            # A completed graph is not authorization to bypass the durable
            # turn/model ledger (conflicts, opt-out and uncertain outcomes).
            response=state.values.get('response')
            if not isinstance(response,dict):
                raise HTTPException(status_code=409,detail={"code":"CHECKPOINT_NOT_RESUMABLE"})
            result=model_step(response) if payload.use_model else persist_step(response)
            return {**result,'orchestrator':'langgraph'}
    return run_turn_graph(retrieve,model_step,persist_step,payload.use_model,
        checkpointer=saver,thread_id=thread_id,resume=payload.resume)



@app.get("/v1/agent/session-deletions")
def get_session_deletions(after_id: int = Query(default=0, ge=0, le=MAX_SYNC_CURSOR), principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    rows = db.deleted_sessions(principal.owner_id, after=after_id, limit=101)
    page = rows[:100]
    return {"deletions":[{"sessionId":r.session_id,"deletedAt":r.deleted_at} for r in page],
            "nextCursor":page[-1].id if page else after_id,"hasMore":len(rows)>100}


@app.delete("/v1/agent/sessions/{session_id}")
def delete_agent_session(session_id: str, payload: SessionDeletion, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if not 1 <= len(session_id) <= 160:
        raise HTTPException(status_code=422, detail={"code":"INVALID_SESSION_ID"})
    db.delete_session(principal.owner_id, principal.device_id, session_id)
    return {"sessionId":session_id,"deleted":True,"recreationBlocked":True}


@app.get("/v1/agent/sessions/{session_id}/turns")
def get_agent_turns(session_id: str, after_id: int = Query(default=0, ge=0, le=MAX_SYNC_CURSOR), principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    if not 1 <= len(session_id) <= 160:
        raise HTTPException(status_code=422, detail={"code":"INVALID_SESSION_ID"})
    rows = db.turns(principal.owner_id,session_id,after=after_id,limit=101)
    page = rows[:100]
    return {"turns":[{"turnId":r.turn_id,"deviceId":r.device_id,"query":r.query,"response":r.response_json,"createdAt":r.created_at} for r in page],
            "nextCursor":page[-1].id if page else after_id,"hasMore":len(rows)>100}



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
    if receipt.state == action.state:
        if receipt.result_code != action.result_code:
            raise HTTPException(status_code=409, detail={"code": "RECEIPT_CONTENT_CONFLICT"})
        return {"actionId": action_id, "state": action.state, "resultCode": action.result_code, "executionAuthorized": False, "duplicate": True}
    allowed = {
        "awaiting_local_confirmation": {"queued", "declined", "expired", "cancelled"},
        "queued": {"running", "failed", "cancelled"},
        "running": {"succeeded", "failed", "cancelled"},
        "proposed": {"declined", "expired", "cancelled"},
    }
    if receipt.state not in allowed.get(action.state, set()):
        raise HTTPException(status_code=409, detail={"code": "INVALID_ACTION_TRANSITION", "state": action.state})
    # Expiry limits accepting a proposal. Work accepted while valid may finish
    # after that time and must still be allowed to report its terminal result.
    if _as_utc(action.expires_at) <= utcnow() and action.state == "awaiting_local_confirmation" and receipt.state == "queued":
        db.transition_action(action, "expired")
        raise HTTPException(status_code=409, detail={"code": "PROPOSAL_EXPIRED"})
    try:
        db.transition_action(action, receipt.state, receipt.result_code)
    except StoreConflict as exc:
        latest = db.action(principal.owner_id, action_id)
        if latest and latest.state == receipt.state and latest.result_code == receipt.result_code:
            return {"actionId": action_id, "state": latest.state, "resultCode": latest.result_code, "executionAuthorized": False, "duplicate": True}
        raise HTTPException(status_code=409, detail={"code": "INVALID_ACTION_TRANSITION"}) from exc
    return {"actionId": action_id, "state": action.state, "resultCode": action.result_code, "executionAuthorized": False, "duplicate": False}


@app.get("/v1/agent/actions/{action_id}")
def get_action_status(action_id: str, principal: Principal = Depends(require_principal), db: Any = Depends(get_db)):
    _device(db, principal)
    action = db.action(principal.owner_id, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail={"code": "ACTION_NOT_FOUND"})
    return {"actionId": action.id, "deviceId": action.device_id, "state": action.state,
            "resultCode": action.result_code, "updatedAt": action.state_updated_at,
            "executionAuthorized": False}


def socket_events(application, principal, cursor):
    # Each blocking store operation runs in a worker thread, never on the
    # WebSocket event loop. A session is created and closed within that thread.
    with open_store(application) as store:
        device = store.device(principal.owner_id, principal.device_id)
        if device is None or device.revoked or device.origin != principal.origin:
            raise DeviceUnavailable()
        return [(row.body_json, row.id) for row in store.events(principal.owner_id, after=cursor, limit=100)]


@app.websocket("/v1/ws")
async def sync_socket(websocket: WebSocket):
    import asyncio
    from .auth import principal_from_token

    await websocket.accept()
    await websocket.send_json({"type": "auth.required"})
    try:
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.close(code=4401, reason="AUTH_TIMEOUT")
            return
        token = message.get("token") if isinstance(message, dict) else None
        settings: Settings = websocket.app.state.settings
        if not isinstance(token, str) or not token:
            await websocket.close(code=4401, reason="AUTH_REQUIRED")
            return
        try:
            principal = await asyncio.to_thread(principal_from_token, token, settings)
        except Exception:
            await websocket.close(code=4403, reason="AUTH_PROVIDER_NOT_BOUND_FOR_WEBSOCKET")
            return
        is_resume = "afterCursor" in message
        cursor = message.get("afterCursor", 0)
        if not isinstance(cursor, int) or isinstance(cursor, bool) or not 0 <= cursor <= MAX_SYNC_CURSOR:
            await websocket.close(code=4400, reason="INVALID_CURSOR")
            return
        while True:
            events = await asyncio.to_thread(socket_events, websocket.app, principal, cursor)
            for event, event_cursor in events:
                await websocket.send_json({"type": "sync.event", "event": event, "cursor": event_cursor})
                cursor = event_cursor
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
                await asyncio.to_thread(principal_from_token, token, settings)
            except Exception:
                await websocket.close(code=4401, reason="SESSION_EXPIRED_OR_INVALID")
                return
            events = await asyncio.to_thread(socket_events, websocket.app, principal, cursor)
            for event, event_cursor in events:
                await websocket.send_json({"type": "sync.event", "event": event, "cursor": event_cursor})
                cursor = event_cursor
    except DeviceUnavailable:
        await websocket.close(code=4403, reason="DEVICE_REVOKED_OR_UNREGISTERED")
    except StoreUnavailable:
        await websocket.close(code=1013, reason="DATABASE_UNAVAILABLE")
    except (WebSocketDisconnect, ValueError, TypeError):
        return
