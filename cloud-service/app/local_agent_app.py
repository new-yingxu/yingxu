"""Loopback-only LangGraph Agent sidecar for the desktop installer.

This ASGI entry point is deliberately separate from ``app.main:app``. CloudBase
continues to host the shared account/sync API; the local sidecar owns model
requests and typed tool handoff. It must be launched by the desktop host with a
per-process bearer token and a private SQLite path.
"""
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Any
from hmac import compare_digest

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .auth import Principal
from .config import Settings
from .db import Base, build_database
from .local_step_store import LocalStep
from .local_tool_protocol import (
    ContinuationRequest,
    InvalidLocalState,
    LocalStateExpired,
    LocalTurnRequest,
)
from .local_turn_service import LocalTurnError, LocalTurnService
from .models import Device
from .store import DeviceUnavailable, SQLStore, SessionDeleted, StoreConflict, StoreUnavailable

LOCAL_OWNER = "local-desktop-owner"
LOCAL_DEVICE = "local-desktop-device"
LOCAL_ISSUER = "yingxu-local-agent"
LOCAL_AUDIENCE = "yingxu-local-agent-api"
_local_bearer = HTTPBearer(auto_error=False)


def _development_signing_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _local_settings(settings: Settings | None, token: str | None) -> tuple[Settings, str]:
    source = settings or Settings.from_env()
    runtime_token = token or os.getenv("YINGXU_LOCAL_AGENT_TOKEN")
    if os.getenv("YINGXU_LOCAL_AGENT_MODE") != "1":
        raise RuntimeError("local Agent sidecar requires explicit YINGXU_LOCAL_AGENT_MODE=1")
    local_database = os.getenv("YINGXU_LOCAL_AGENT_DATABASE_URL") or source.database_url
    if not isinstance(local_database, str) or not local_database.startswith("sqlite:"):
        raise RuntimeError("local Agent sidecar requires a private SQLite database")
    if not runtime_token or len(runtime_token) < 32:
        raise RuntimeError("desktop host must provide a high-entropy local Agent bearer token")
    # Packaged builds may inherit APP_ENV=production and cloud credentials from
    # their parent. The loopback sidecar intentionally ignores those settings.
    # A stable key must come from the desktop host's OS credential store so a
    # pending tool continuation remains verifiable after a sidecar restart.
    local_signing_key = os.getenv("YINGXU_LOCAL_AGENT_SIGNING_KEY")
    if source.app_env != "development" and not local_signing_key:
        raise RuntimeError("packaged local Agent requires a signing key from the OS credential store")
    model = os.getenv("YINGXU_LOCAL_AGENT_MODEL", "qwen2.5:7b")
    credentials = json.dumps({LOCAL_OWNER: {"provider": "ollama", "model": model}})
    try:
        from .model_provider import credential_for_owner
        credential_for_owner(credentials, LOCAL_OWNER, allow_local=True)
    except ValueError:
        raise RuntimeError("local Agent model must be a supported Ollama model name") from None
    resolved = replace(
        source,
        app_env="development",  # isolated local runtime; never enables cloud app routes
        database_url=local_database,
        auth_mode="dev",
        dev_auth_token=runtime_token,
        jwt_issuer=LOCAL_ISSUER,
        jwt_audience=LOCAL_AUDIENCE,
        session_private_key=local_signing_key or _development_signing_key(),
        session_key_id=os.getenv("YINGXU_LOCAL_AGENT_KEY_ID") or "local-agent-process",
        session_verification_jwks=None,
        model_owner_credentials=credentials,
        cloudbase_env_id=None,
        cloudbase_api_key=None,
        cloudbase_client_origins=None,
    )
    return resolved, runtime_token


def create_local_agent_app(*, settings: Settings | None = None, token: str | None = None) -> FastAPI:
    """Create the desktop-only Agent API; it refuses production/CloudBase mode."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            resolved, runtime_token = _local_settings(settings, token)
        except RuntimeError as exc:
            app.state.startup_error = str(exc)
            raise
        config_errors = resolved.configuration_errors()
        if config_errors:
            raise RuntimeError("invalid local Agent sidecar configuration")

        # Register all SQLAlchemy models before creating the per-user local DB.
        from . import graph_checkpoints, preset_store, sync_snapshots  # noqa: F401

        engine, session_factory = build_database(resolved)
        if engine is None or engine.dialect.name != "sqlite":
            raise RuntimeError("local Agent sidecar refuses a non-SQLite database")
        Base.metadata.create_all(engine)
        app.state.settings = resolved
        app.state.local_agent_token = runtime_token
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.config_errors = []
        app.state.startup_error = None
        with _store_factory(app) as store:
            current = store.device(LOCAL_OWNER, LOCAL_DEVICE)
            if current is None:
                store.save_device(Device(
                    owner_id=LOCAL_OWNER,
                    device_id=LOCAL_DEVICE,
                    origin="desktop",
                    display_name="This computer",
                ))
            elif current.revoked or current.origin != "desktop":
                raise RuntimeError("local Agent device registration is not active")
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Yingxu Local Agent Sidecar", version="1", lifespan=lifespan)

    @app.exception_handler(StoreUnavailable)
    async def unavailable(_: Request, exc: StoreUnavailable):
        return JSONResponse(status_code=503, content={"detail": {"code": "LOCAL_AGENT_STORE_UNAVAILABLE"}})

    @app.exception_handler(StoreConflict)
    async def conflict(_: Request, exc: StoreConflict):
        return JSONResponse(status_code=409, content={"detail": {"code": "LOCAL_AGENT_CONFLICT"}})

    @app.exception_handler(DeviceUnavailable)
    async def device_revoked(_: Request, exc: DeviceUnavailable):
        return JSONResponse(status_code=403, content={"detail": {"code": "LOCAL_AGENT_DEVICE_UNAVAILABLE"}})

    @app.exception_handler(SessionDeleted)
    async def deleted_session(_: Request, exc: SessionDeleted):
        return JSONResponse(status_code=410, content={"detail": {"code": "SESSION_DELETED"}})

    @app.exception_handler(LocalTurnError)
    async def local_turn_error(_: Request, exc: LocalTurnError):
        return JSONResponse(status_code=exc.status, content={
            "detail": {"code": exc.code, "automaticRetryAllowed": False}
        })

    @app.exception_handler(LocalStateExpired)
    async def expired_state(_: Request, exc: LocalStateExpired):
        return JSONResponse(status_code=410, content={"detail": {"code": "LOCAL_TOOL_STATE_EXPIRED"}})

    @app.exception_handler(InvalidLocalState)
    async def invalid_state(_: Request, exc: InvalidLocalState):
        return JSONResponse(status_code=422, content={"detail": {"code": "LOCAL_TOOL_STATE_INVALID"}})

    @app.get("/healthz")
    def healthz(request: Request):
        return {"ok": True, "service": "yingxu-local-agent", "version": request.app.version}

    @app.get("/v1/agent/local-status")
    def local_status(
        request: Request,
        principal: Principal = Depends(_require_local_principal),
    ):
        _check_local_runtime(request, principal)
        from .model_provider import credential_for_owner, local_ollama_model_state
        credential = credential_for_owner(request.app.state.settings.model_owner_credentials, LOCAL_OWNER, allow_local=True)
        availability = local_ollama_model_state(credential)
        return {
            "configured": availability == "ready",
            "provider": credential.provider,
            "model": credential.model,
            "region": credential.region,
            "availability": availability,
            "requiresExplicitConsent": True,
            "executionAuthorized": False,
            "cloudFallback": False,
        }

    @app.post("/_host/shutdown")
    def host_shutdown(
        request: Request,
        principal: Principal = Depends(_require_local_principal),
    ):
        _check_local_runtime(request, principal)
        request_shutdown = getattr(request.app.state, "request_shutdown", None)
        if not callable(request_shutdown):
            raise HTTPException(status_code=503, detail={"code": "LOCAL_AGENT_SHUTDOWN_UNAVAILABLE"})
        request_shutdown()
        return {"stopping": True}

    @app.post("/v1/agent/local-turns")
    def start_local_turn(
        payload: LocalTurnRequest,
        request: Request,
        principal: Principal = Depends(_require_local_principal),
    ):
        _check_local_runtime(request, principal)
        return _service(request, principal).start(payload)

    @app.post("/v1/agent/local-turns/{session_id}/{turn_id}/results")
    def continue_local_turn(
        session_id: str,
        turn_id: str,
        payload: ContinuationRequest,
        request: Request,
        principal: Principal = Depends(_require_local_principal),
    ):
        _check_local_runtime(request, principal)
        return _service(request, principal).continue_turn(session_id, turn_id, payload)

    return app


@contextmanager
def _store_factory(app: FastAPI):
    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        raise StoreUnavailable("local Agent database is unavailable")
    store = SQLStore(session_factory())
    try:
        yield store
    finally:
        store.close()


def _check_local_runtime(request: Request, principal: Principal) -> None:
    if getattr(request.app.state, "startup_error", None):
        raise HTTPException(status_code=503, detail={"code": "LOCAL_AGENT_NOT_STARTED"})
    if principal != Principal(LOCAL_OWNER, LOCAL_DEVICE, "desktop"):
        raise HTTPException(status_code=403, detail={"code": "LOCAL_AGENT_IDENTITY_INVALID"})


def _require_local_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_local_bearer),
) -> Principal:
    expected = getattr(request.app.state, "local_agent_token", None)
    supplied = credentials.credentials if credentials is not None else ""
    if not isinstance(expected, str) or not expected or not compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail={"code": "LOCAL_AGENT_AUTH_REQUIRED"})
    return Principal(LOCAL_OWNER, LOCAL_DEVICE, "desktop")


def _service(request: Request, principal: Principal) -> LocalTurnService:
    return LocalTurnService(
        lambda: _store_factory(request.app),
        request.app.state.settings,
        principal,
    )


# The desktop launcher supplies YINGXU_LOCAL_AGENT_MODE and a fresh bearer token.
# Binding the runner to 127.0.0.1 remains the launcher/host's responsibility.
app = create_local_agent_app()
