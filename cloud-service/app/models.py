from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncOwnerLock(Base):
    __tablename__ = "sync_owner_locks"
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("owner_id", "device_id", name="uq_device_owner_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), index=True)
    device_id: Mapped[str] = mapped_column(String(500))
    origin: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncEvent(Base):
    __tablename__ = "sync_events"
    __table_args__ = (
        UniqueConstraint("owner_id", "event_id", name="uq_sync_owner_event"),
        UniqueConstraint("owner_id", "device_id", "sequence", name="uq_sync_device_sequence"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), index=True)
    device_id: Mapped[str] = mapped_column(String(500), index=True)
    event_id: Mapped[str] = mapped_column(String(500))
    sequence: Mapped[int] = mapped_column(BigInteger)
    body_json: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentAction(Base):
    __tablename__ = "agent_actions"
    id: Mapped[str] = mapped_column(String(500), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True, index=True)
    device_id: Mapped[str] = mapped_column(String(500))
    origin: Mapped[str] = mapped_column(String(16))
    tool: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(40), default="proposed")
    proposal_json: Mapped[dict] = mapped_column(JSON)
    result_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SyncPreferences(Base):
    __tablename__ = "sync_preferences"
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    sync_records: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_agent_sessions: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_thumbnails: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_paths: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (UniqueConstraint("owner_id", "session_id", "turn_id", name="uq_agent_turn_identity"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), index=True)
    session_id: Mapped[str] = mapped_column(String(160))
    turn_id: Mapped[str] = mapped_column(String(160))
    device_id: Mapped[str] = mapped_column(String(500))
    query: Mapped[str] = mapped_column(String(500))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeletedAgentSession(Base):
    __tablename__ = "deleted_agent_sessions"
    __table_args__ = (UniqueConstraint("owner_id", "session_id", name="uq_deleted_session"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), index=True)
    session_id: Mapped[str] = mapped_column(String(160))
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncInvalidation(Base):
    __tablename__ = "sync_invalidations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentModelRequest(Base):
    __tablename__ = "agent_model_requests"
    owner_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(500))
    request_hash: Mapped[str] = mapped_column(String(64))
    attempt_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
