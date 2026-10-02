"""Shared schema. Services own queries, locking, and immutable-record enforcement."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from durable_agent.contracts import AttemptStatus, JsonObject, RunStatus


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    forked_from_operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", name="fk_conversations_forked_from_operation", use_alter=True)
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("submission_key", name="uq_runs_submission_key"),
        UniqueConstraint("conversation_id", "turn_index", name="uq_runs_conversation_turn"),
        CheckConstraint("turn_index >= 0", name="ck_runs_turn_index"),
        UniqueConstraint("parent_operation_id", "child_index", name="uq_runs_parent_child"),
        CheckConstraint(
            "(parent_operation_id IS NULL AND child_index IS NULL) OR "
            "(parent_operation_id IS NOT NULL AND child_index IS NOT NULL "
            "AND child_index BETWEEN 0 AND 2)",
            name="ck_runs_child_identity",
        ),
        Index(
            "uq_runs_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'retrying', 'waiting')"),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'waiting', 'succeeded', 'failed')",
            name="ck_runs_status",
        ),
        CheckConstraint("lease_generation >= 0", name="ck_runs_generation"),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_generation > 0) OR "
            "(status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_runs_lease",
        ),
        CheckConstraint(
            "(status IN ('queued', 'retrying')) = (next_attempt_at IS NOT NULL)",
            name="ck_runs_claim_time",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_runs_finished",
        ),
        CheckConstraint(
            "(status = 'failed') = (terminal_error IS NOT NULL)", name="ck_runs_terminal_error"
        ),
        Index(
            "ix_runs_claimable",
            "next_attempt_at",
            postgresql_where=text("status IN ('queued', 'retrying')"),
        ),
        Index(
            "ix_runs_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    parent_operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", name="fk_runs_parent_operation", use_alter=True)
    )
    child_index: Mapped[int | None] = mapped_column(Integer)
    turn_index: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    submission_key: Mapped[UUID] = mapped_column(Uuid)
    prompt: Mapped[str] = mapped_column(Text)
    config: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[RunStatus] = mapped_column(Text)
    pause_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[UUID | None] = mapped_column(Uuid)
    lease_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_error: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("run_id", "position", name="uq_operations_run_position"),
        CheckConstraint("position >= 0", name="ck_operations_position"),
        CheckConstraint("kind IN ('model', 'tool')", name="ck_operations_kind"),
        CheckConstraint(
            "(result IS NULL) = (completed_at IS NULL)", name="ck_operations_completion"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    request: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    result: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("operation_id", "attempt_no", name="uq_attempts_operation_number"),
        CheckConstraint("attempt_no > 0", name="ck_attempts_number"),
        CheckConstraint("lease_generation > 0", name="ck_attempts_generation"),
        CheckConstraint(
            "status IN ('running', 'accepted', 'error', 'unknown')", name="ck_attempts_status"
        ),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="ck_attempts_finished"
        ),
        Index(
            "uq_attempts_one_active_or_accepted",
            "operation_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'accepted')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("operations.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[UUID] = mapped_column(Uuid)
    lease_generation: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[AttemptStatus] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))


class WorkspaceVersion(Base):
    __tablename__ = "workspace_versions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    archive: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolRequest(Base):
    __tablename__ = "tool_requests"
    __table_args__ = (
        CheckConstraint("lease_generation > 0", name="ck_tool_requests_generation"),
        CheckConstraint(
            "(result IS NULL AND output_version_id IS NULL AND completed_at IS NULL) OR "
            "(result IS NOT NULL AND output_version_id IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_tool_requests_completion",
        ),
    )

    operation_key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    request: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    input_version_id: Mapped[UUID] = mapped_column(ForeignKey("workspace_versions.id"))
    lease_generation: Mapped[int] = mapped_column(BigInteger)
    result: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    output_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspace_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
