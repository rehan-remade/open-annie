"""Close every minted session at its cap, when idle, or when the budget runs out.

Preferred path: a sideband WebSocket (wss://api.openai.com/v1/live/sessions/{id}/attach)
that reads usage and activity and sends `session.close`. If the sideband is off or
cannot attach, fall back to wall-clock: wait for the cap, try the hangup endpoint,
and bill the elapsed time. Transcript text is inspected for nothing and kept nowhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any, Callable

from .budget import Budget
from .config import Settings
from .voice import VoiceClient

log = logging.getLogger("annie.reaper")

CLOSE_DRAIN_S = 10.0
ACTIVITY_EVENTS = {"session.input_transcript.delta", "session.output_transcript.delta"}


def _default_connect(url: str, headers: dict[str, str]) -> Any:
    from websockets.asyncio.client import connect

    return connect(url, additional_headers=headers, open_timeout=5, max_size=2**22)


class Reaper:
    def __init__(
        self,
        settings: Settings,
        budget: Budget,
        voice: VoiceClient,
        connect: Callable[[str, dict[str, str]], Any] | None = None,
        clock: Callable[[], float] = time.time,
        tick_s: float = 1.0,
    ):
        self.s, self.budget, self.voice = settings, budget, voice
        self.connect = connect or _default_connect
        self.clock = clock
        self.tick_s = tick_s
        self.tasks: dict[str, asyncio.Task] = {}

    def watch(self, session_id: str) -> None:
        task = asyncio.ensure_future(self._run(session_id))
        self.tasks[session_id] = task
        task.add_done_callback(lambda _t: self.tasks.pop(session_id, None))

    async def _run(self, session_id: str) -> None:
        final: float | None = None
        try:
            if self.s.sideband:
                final = await self._sideband(session_id)
            if final is None:
                await self._wall_clock(session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let a reaper bug leak a session slot
            log.warning("reaper error for session: %s", type(exc).__name__)
        finally:
            self.budget.close(session_id, final)

    def _close_reason(self, session_id: str) -> str | None:
        rec = self.budget.sessions.get(session_id)
        if rec is None:
            return "gone"
        now = self.clock()
        if now >= rec.deadline:
            return "cap"
        if self.budget.exhausted():
            return "budget"
        if now - rec.last_activity >= self.s.idle_timeout_s:
            return "idle"
        return None

    async def _sideband(self, session_id: str) -> float | None:
        """Return final usage seconds from session.closed, or None if unconfirmed."""
        url = self.s.openai_base_url.replace("https://", "wss://", 1)
        url = f"{url}/v1/live/sessions/{session_id}/attach"
        headers = {"Authorization": f"Bearer {self.s.openai_api_key}"}
        try:
            async with self.connect(url, headers) as ws:
                return await self._pump(session_id, ws)
        except Exception as exc:
            log.info("sideband unavailable (%s); using wall-clock reaping", type(exc).__name__)
            return None

    async def _pump(self, session_id: str, ws: Any) -> float | None:
        close_sent_at: float | None = None
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), self.tick_s)
            except TimeoutError:
                raw = None
            if raw is not None:
                final = self._handle(session_id, raw)
                if final is not None:
                    return final
            if close_sent_at is None:
                reason = self._close_reason(session_id)
                if reason:
                    log.info("closing session: reason=%s", reason)
                    await ws.send(json.dumps({"type": "session.close", "event_id": f"annie_{reason}"}))
                    close_sent_at = self.clock()
            elif self.clock() - close_sent_at > CLOSE_DRAIN_S:
                return None

    def _handle(self, session_id: str, raw: str | bytes) -> float | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        # The sideband reflects both audio streams (~64 KB/s of base64 each).
        # Skip parsing those; the type names can't occur inside base64.
        if len(raw) > 4096:
            if "session.output_audio.delta" in raw:
                self.budget.activity(session_id)
            return None
        try:
            event = json.loads(raw)
        except ValueError:
            return None
        kind = event.get("type")
        if kind in ACTIVITY_EVENTS:
            self.budget.activity(session_id)
        elif kind == "session.usage.updated":
            seconds = (event.get("usage") or {}).get("seconds")
            if isinstance(seconds, (int, float)):
                self.budget.usage(session_id, seconds)
        elif kind == "session.closed":
            seconds = (event.get("usage") or {}).get("seconds")
            log.info("session closed: reason=%s", event.get("reason"))
            rec = self.budget.sessions.get(session_id)
            if isinstance(seconds, (int, float)):
                return float(seconds)
            return rec.usage_seconds if rec and rec.usage_seconds is not None else 0.0
        return None

    async def _wall_clock(self, session_id: str) -> None:
        rec = self.budget.sessions.get(session_id)
        if rec is None:
            return
        rec.usage_seconds = None  # unconfirmed from here on: bill wall-clock, conservatively
        while (reason := self._close_reason(session_id)) is None or reason == "idle":
            # Without the sideband there is no activity signal, so idle can't be judged.
            await asyncio.sleep(self.tick_s)
        if reason != "gone" and self.s.hangup_fallback:
            await self.voice.hangup(session_id)

    async def aclose(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
