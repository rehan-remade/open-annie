import asyncio
import json
from dataclasses import replace

import pytest

from annie_broker import tokens
from annie_broker.budget import Budget, Rejected
from annie_broker.reaper import Reaper
from conftest import BASE

SECRET = b"k"


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# -- tokens ------------------------------------------------------------------


def test_token_roundtrip_and_expiry():
    tok = tokens.mint(SECRET, "live_abc", 1000)
    assert tokens.verify(SECRET, tok, now=999) == "live_abc"
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.verify(SECRET, tok, now=1000)


def test_token_rejects_tampering():
    tok = tokens.mint(SECRET, "live_abc", 1000)
    sid, exp, sig = tok.rsplit(".", 2)
    for bad in (f"live_xyz.{exp}.{sig}", f"{sid}.2000.{sig}", tok + "x", "x", None, 5):
        with pytest.raises(tokens.TokenError):
            tokens.verify(SECRET, bad, now=0)
    with pytest.raises(tokens.TokenError, match="bad_signature"):
        tokens.verify(b"other", tok, now=0)


def test_token_allows_dotted_ids():
    assert tokens.verify(SECRET, tokens.mint(SECRET, "a.b.c", 10), now=0) == "a.b.c"


# -- budget ------------------------------------------------------------------


def test_budget_accrues_wall_clock_and_usage():
    clock = Clock()
    b = Budget(replace(BASE, daily_budget_usd=1.0), clock)
    b.open(b.admit("1.2.3.4"), "s1")
    assert b.voice_seconds_today() == 15  # WebRTC create pre-bills 15 s
    clock.t += 60
    assert b.voice_seconds_today() == 60
    b.usage("s1", 42)
    assert b.voice_seconds_today() == 42  # upstream usage beats the wall-clock estimate
    b.close("s1", final_seconds=50)
    assert b.voice_seconds_today() == 50
    assert b.spent_today() == pytest.approx(50 * 0.05 / 60)
    assert b.status()["active_sessions"] == 0


def test_budget_admission_reserves_worst_case():
    b = Budget(replace(BASE, daily_budget_usd=0.31, session_cap_s=180), Clock())
    b.open(b.admit("a"), "s1")
    b.open(b.admit("b"), "s2")  # 2 x $0.15 reserved
    with pytest.raises(Rejected, match="budget_exhausted"):
        b.admit("c")


def test_budget_resets_at_utc_midnight():
    clock = Clock(1_790_035_199.0)  # 23:59:59 UTC
    b = Budget(replace(BASE, per_ip_daily=1), clock)
    b.close("x")  # no-op for unknown ids
    b.open(b.admit("ip"), "s1")
    b.close("s1", 100)
    with pytest.raises(Rejected, match="ip_limit"):
        b.admit("ip")
    clock.t += 2
    assert b.spent_today() == 0
    b.admit("ip")


def test_ips_are_hashed():
    b = Budget(replace(BASE, per_ip_daily=5), Clock())
    b.admit("203.0.113.7")
    assert "203.0.113.7" not in json.dumps(b._ip_counts)


# -- reaper --------------------------------------------------------------------


class FakeSideband:
    def __init__(self, events):
        self.inbox: asyncio.Queue = asyncio.Queue()
        for e in events:
            self.inbox.put_nowait(e if isinstance(e, str) else json.dumps(e))
        self.sent: list[dict] = []
        self.url = self.headers = None

    def __call__(self, url, headers):
        self.url, self.headers = url, headers
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        return await self.inbox.get()

    async def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["type"] == "session.close":
            self.inbox.put_nowait(json.dumps(
                {"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 181.5}}
            ))


class NoVoice:
    hangups: list = []

    async def hangup(self, session_id):
        self.hangups.append(session_id)
        return True


async def _run_reaper(events, clock, **overrides):
    s = replace(BASE, sideband=True, **overrides)
    budget = Budget(s, clock)
    budget.open(budget.admit("ip"), "live_1")
    ws = FakeSideband(events)
    reaper = Reaper(s, budget, NoVoice(), connect=ws, clock=clock, tick_s=0.01)
    reaper.watch("live_1")
    return budget, ws, reaper


async def test_reaper_records_final_usage_from_session_closed():
    clock = Clock()
    budget, ws, reaper = await _run_reaper(
        [
            {"type": "session.usage.updated", "usage": {"seconds": 12}},
            {"type": "session.input_transcript.delta", "delta": "hi", "start_ms": 0, "end_ms": 5},
            {"type": "session.closed", "reason": "remote_hangup", "usage": {"seconds": 33}},
        ],
        clock,
    )
    await asyncio.wait_for(asyncio.gather(*reaper.tasks.values()), 2)
    assert ws.url == "wss://api.openai.com/v1/live/sessions/live_1/attach"
    assert ws.headers == {"Authorization": "Bearer sk-test"}
    assert budget.sessions == {} and budget.voice_seconds_today() == 33
    assert ws.sent == []


async def test_reaper_closes_at_cap():
    clock = Clock()
    budget, ws, reaper = await _run_reaper([], clock, idle_timeout_s=10_000)
    await asyncio.sleep(0.05)
    assert ws.sent == []
    clock.t += 181
    await asyncio.wait_for(asyncio.gather(*reaper.tasks.values()), 2)
    assert ws.sent == [{"type": "session.close", "event_id": "annie_cap"}]
    assert budget.voice_seconds_today() == 181.5


async def test_reaper_closes_when_idle():
    clock = Clock()
    budget, ws, reaper = await _run_reaper([], clock, idle_timeout_s=30)
    clock.t += 31
    await asyncio.wait_for(asyncio.gather(*reaper.tasks.values()), 2)
    assert ws.sent[0]["event_id"] == "annie_idle"


async def test_reaper_skips_reflected_audio_without_parsing():
    clock = Clock()
    audio = '{"type":"session.output_audio.delta","delta":"' + "A" * 8000 + '"}'
    budget, ws, reaper = await _run_reaper([audio], clock, idle_timeout_s=30)
    clock.t += 20
    await asyncio.sleep(0.05)
    assert budget.sessions["live_1"].last_activity == clock.t  # assistant speech counts as activity
    await reaper.aclose()


async def test_reaper_falls_back_to_wall_clock():
    clock = Clock()

    def broken(url, headers):
        raise OSError("no sideband")

    s = replace(BASE, sideband=True, hangup_fallback=True)
    budget = Budget(s, clock)
    budget.open(budget.admit("ip"), "live_2")
    voice = NoVoice()
    reaper = Reaper(s, budget, voice, connect=broken, clock=clock, tick_s=0.01)
    reaper.watch("live_2")
    clock.t += 31  # idle can't be judged without the sideband: keeps running
    await asyncio.sleep(0.05)
    assert "live_2" in budget.sessions
    clock.t += 150
    await asyncio.wait_for(asyncio.gather(*reaper.tasks.values()), 2)
    assert voice.hangups[-1] == "live_2"
    assert budget.voice_seconds_today() == 181
