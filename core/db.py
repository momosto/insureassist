"""Persistence (SQLAlchemy 2): conversations, turns, tool calls, handoffs, outbound messages, opt-outs, MCP audit.

Transcripts are stored redacted (no national IDs, card-like numbers or OTP/confirmation codes).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(20))
    wa_id: Mapped[str] = mapped_column(String(20), index=True)
    customer_ref: Mapped[str | None] = mapped_column(String(30), nullable=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    status: Mapped[str] = mapped_column(String(20), default="BOT")  # BOT | HANDOFF | HUMAN | CLOSED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    turns: Mapped[list["Turn"]] = relationship(back_populates="conversation", order_by="Turn.id")


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # customer | assistant | agent | system
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verdicts: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    author: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED | ASSIGNED | RETURNED | CLOSED
    assignee: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboundMessage(Base):
    """Every message sent to a customer (also the simulator's outbox when no WhatsApp token is configured)."""
    __tablename__ = "outbound_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # text | template | sms
    template: Mapped[str | None] = mapped_column(String(60), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedMessage(Base):
    """Idempotency for inbound webhooks and bus events (at-least-once delivery)."""
    __tablename__ = "processed_messages"
    message_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptOut(Base):
    __tablename__ = "opt_outs"
    wa_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentIntent(Base):
    """Audit trail for ADR-0004: intent → confirmation → payment id → provider result."""
    __tablename__ = "payment_intents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_ref: Mapped[str] = mapped_column(String(30))
    wa_id: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    reference: Mapped[str] = mapped_column(String(40), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # PENDING | CONFIRMED | EXPIRED | CANCELLED | SUCCEEDED | FAILED
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class ToolAudit(Base):
    """MCP-server-side audit of every tool call (args redacted)."""
    __tablename__ = "mcp_tool_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String(40))
    customer_ref: Mapped[str | None] = mapped_column(String(30), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(30))
    duration_ms: Mapped[float] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_session_factory(url: str):
    if url in ("sqlite://", "sqlite:///:memory:"):  # tests and evals: one shared in-memory database
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
                               pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def db_audit_sink(session_factory):
    def record(r: dict) -> None:
        with session_factory() as s:
            s.add(ToolAudit(tool=r["tool"], customer_ref=r["customer_ref"], conversation_id=r.get("conversation_id"),
                            args=r["args"], outcome=r["outcome"], duration_ms=int(r["duration_ms"])))
            s.commit()
    return record
