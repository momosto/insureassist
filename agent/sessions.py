"""Conversation state per WhatsApp user (architecture §5): Redis in production, in-memory for the demo and tests."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class PendingPayment:
    intent_id: str
    kind: str  # premium | loan
    reference: str
    currency: str
    amount: str
    description: str
    code: str
    expires_at: float
    attempts: int = 0


@dataclass
class Session:
    wa_id: str
    conversation_id: str | None = None
    state: str = "NEW"  # NEW | AWAITING_OTP | VERIFIED
    customer_ref: str | None = None
    first_name: str | None = None
    language: str = "en"
    otp_hash: str | None = None
    otp_expires_at: float = 0
    otp_attempts: int = 0
    locked_until: float = 0
    token: str | None = None
    token_expires_at: float = 0
    parked_text: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)  # text-only user/assistant turns sent to the model
    pending: PendingPayment | None = None
    tool_failures: int = 0
    negative_turns: int = 0
    last_seen: float = field(default_factory=time.time)
    day: str = ""
    turns_today: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "Session":
        data = json.loads(raw)
        pending = data.pop("pending", None)
        s = Session(**data)
        s.pending = PendingPayment(**pending) if pending else None
        return s

    @property
    def verified(self) -> bool:
        return self.state == "VERIFIED" and self.token is not None and self.token_expires_at > time.time()


class SessionStore(Protocol):
    def get(self, wa_id: str) -> Session | None: ...
    def save(self, session: Session, ttl_seconds: int) -> None: ...
    def delete(self, wa_id: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def get(self, wa_id: str) -> Session | None:
        with self._lock:
            item = self._data.get(wa_id)
            if not item or item[1] < time.time():
                self._data.pop(wa_id, None)
                return None
            return Session.from_json(item[0])

    def save(self, session: Session, ttl_seconds: int) -> None:
        with self._lock:
            self._data[session.wa_id] = (session.to_json(), time.time() + ttl_seconds)

    def delete(self, wa_id: str) -> None:
        with self._lock:
            self._data.pop(wa_id, None)


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, wa_id: str) -> Session | None:
        raw = self._r.get(f"session:{wa_id}")
        return Session.from_json(raw) if raw else None

    def save(self, session: Session, ttl_seconds: int) -> None:
        self._r.set(f"session:{session.wa_id}", session.to_json(), ex=ttl_seconds)

    def delete(self, wa_id: str) -> None:
        self._r.delete(f"session:{wa_id}")
