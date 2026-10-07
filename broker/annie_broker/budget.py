"""In-memory daily spend, per-IP limits, and session admission control.

State lives in one process, so the hosted app runs a single runner
(max_concurrency=1). Spend resets at 00:00 UTC. IPs are stored only as keyed
hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .config import JEV_USD_PER_INPUT_TOKEN, VOICE_INIT_SECONDS, VOICE_USD_PER_SECOND, Settings


class Rejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class SessionRecord:
    session_id: str
    started_at: float
    deadline: float
    last_activity: float
    usage_seconds: float | None = None  # from session.usage.updated, when attached


@dataclass
class Reservation:
    ip_key: str
    done: bool = field(default=False)


class Budget:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.s = settings
        self.clock = clock
        self.sessions: dict[str, SessionRecord] = {}
        self._pending = 0
        self._day = self._today()
        self._closed_seconds = 0.0
        self._jev_usd = 0.0
        self._ip_counts: dict[str, int] = {}

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._closed_seconds, self._jev_usd = today, 0.0, 0.0
            self._ip_counts.clear()

    def ip_key(self, ip: str) -> str:
        return hmac.new(self.s.hmac_secret, ip.encode(), hashlib.sha256).hexdigest()[:24]

    def _billed_seconds(self, rec: SessionRecord, now: float) -> float:
        seconds = rec.usage_seconds if rec.usage_seconds is not None else now - rec.started_at
        return max(float(VOICE_INIT_SECONDS), seconds)

    # -- admission ---------------------------------------------------------

    def admit(self, ip: str) -> Reservation:
        self._roll()
        key = self.ip_key(ip)
        if self.s.per_ip_daily and self._ip_counts.get(key, 0) >= self.s.per_ip_daily:
            raise Rejected("ip_limit")
        if len(self.sessions) + self._pending >= self.s.max_sessions:
            raise Rejected("at_capacity")
        # Reserve each open session at its worst case (the cap) so concurrent
        # sessions can never jointly overrun the budget.
        now = self.clock()
        cap = float(self.s.session_cap_s)
        committed = self._closed_seconds + cap * self._pending + sum(
            max(cap, self._billed_seconds(r, now)) for r in self.sessions.values()
        )
        if (committed + cap) * VOICE_USD_PER_SECOND + self._jev_usd > self.s.daily_budget_usd:
            raise Rejected("budget_exhausted")
        self._pending += 1
        self._ip_counts[key] = self._ip_counts.get(key, 0) + 1
        return Reservation(ip_key=key)

    def cancel(self, res: Reservation) -> None:
        """Upstream mint failed: release the slot and refund the IP count."""
        if res.done:
            return
        res.done = True
        self._pending -= 1
        if self._ip_counts.get(res.ip_key):
            self._ip_counts[res.ip_key] -= 1

    def open(self, res: Reservation, session_id: str) -> SessionRecord:
        res.done = True
        self._pending -= 1
        now = self.clock()
        rec = SessionRecord(session_id, now, now + self.s.session_cap_s, now)
        self.sessions[session_id] = rec
        return rec

    # -- accounting --------------------------------------------------------

    def usage(self, session_id: str, seconds: float) -> None:
        if rec := self.sessions.get(session_id):
            rec.usage_seconds = max(rec.usage_seconds or 0.0, float(seconds))

    def activity(self, session_id: str) -> None:
        if rec := self.sessions.get(session_id):
            rec.last_activity = self.clock()

    def close(self, session_id: str, final_seconds: float | None = None) -> None:
        rec = self.sessions.pop(session_id, None)
        if rec is None:
            return
        self._roll()
        if final_seconds is not None:
            rec.usage_seconds = float(final_seconds)
        self._closed_seconds += self._billed_seconds(rec, self.clock())

    def add_jev(self, input_tokens: int) -> None:
        self._roll()
        self._jev_usd += max(0, int(input_tokens)) * JEV_USD_PER_INPUT_TOKEN

    def voice_seconds_today(self) -> float:
        self._roll()
        now = self.clock()
        return self._closed_seconds + sum(self._billed_seconds(r, now) for r in self.sessions.values())

    def spent_today(self) -> float:
        return self.voice_seconds_today() * VOICE_USD_PER_SECOND + self._jev_usd

    def exhausted(self) -> bool:
        return self.spent_today() >= self.s.daily_budget_usd

    def status(self) -> dict:
        return {
            "spent_today_usd": round(self.spent_today(), 6),
            "budget_usd": self.s.daily_budget_usd,
            "active_sessions": len(self.sessions),
            "max_sessions": self.s.max_sessions,
            "voice_seconds_today": round(self.voice_seconds_today(), 1),
        }
