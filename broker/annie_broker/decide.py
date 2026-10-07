"""Jev proxy: one warm HTTP/2 connection pool, fail fast, never retry.

The browser always has a local rule-based floor, so a late answer is worth less
than no answer: every failure mode becomes DecideUnavailable (503) quickly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import httpx

from .config import Settings

log = logging.getLogger("annie.decide")

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 30.0


class DecideUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DecideInvalid(Exception):
    """Upstream rejected the request shape (400/422); the caller's fault, not an outage."""


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float, clock: Callable[[], float]):
        self.rate, self.burst, self.clock = rate_per_s, burst, clock
        self.tokens, self.stamp = burst, clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class DecideClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.s = settings
        self.clock = clock
        self._client = httpx.AsyncClient(
            base_url=settings.typesafe_base_url,
            headers={"Authorization": f"Bearer {settings.typesafe_api_key}"},
            http2=True,
            timeout=httpx.Timeout(settings.decide_timeout_s),
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300),
            transport=transport,
        )
        self.governor = TokenBucket(settings.jev_rpm / 60.0, settings.jev_burst, clock)
        self._session_buckets: dict[str, TokenBucket] = {}
        self.disabled = False  # set on 401/403; needs a restart with a valid key
        self.failures = 0
        self.open_until = 0.0
        self._warm_task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return bool(self.s.typesafe_api_key) and not self.disabled

    @property
    def breaker_open(self) -> bool:
        return self.failures >= BREAKER_THRESHOLD and self.clock() < self.open_until

    def start_warm(self) -> None:
        """Open the TLS + HTTP/2 connection before the first real decision needs it."""
        if self.available and self._warm_task is None:
            self._warm_task = asyncio.ensure_future(self._warm())

    async def _warm(self) -> None:
        try:
            # /v1/models is free (no tokens) and exercises auth on the same pool.
            resp = await self._client.get("/v1/models", timeout=5.0)
            if resp.status_code in (401, 403):
                self._disable(resp.status_code)
        except httpx.HTTPError as exc:
            log.info("jev warm-up failed: %s", type(exc).__name__)

    def _disable(self, status: int) -> None:
        if not self.disabled:
            log.error("jev rejected the API key (status=%s); /decide disabled", status)
        self.disabled = True

    def _fail(self, reason: str) -> DecideUnavailable:
        self.failures += 1
        if self.failures >= BREAKER_THRESHOLD:
            self.open_until = self.clock() + BREAKER_COOLDOWN_S
            if self.failures == BREAKER_THRESHOLD:
                log.warning("jev circuit breaker open for %.0fs", BREAKER_COOLDOWN_S)
        return DecideUnavailable(reason)

    def _session_allows(self, key: str) -> bool:
        bucket = self._session_buckets.get(key)
        if bucket is None:
            if len(self._session_buckets) > 4096:
                self._session_buckets.clear()
            rate = self.s.decide_per_session_rps
            bucket = self._session_buckets[key] = TokenBucket(rate, rate * 2, self.clock)
        return bucket.take()

    async def decide(self, state: object, questions: dict, session_key: str | None = None) -> dict:
        if not self.available:
            raise DecideUnavailable("no_key" if not self.s.typesafe_api_key else "disabled")
        if self.breaker_open:
            raise DecideUnavailable("breaker_open")
        if session_key is not None and not self._session_allows(session_key):
            raise DecideUnavailable("session_rate_limited")
        if not self.governor.take():
            raise DecideUnavailable("rate_limited")
        self.start_warm()

        body = {"model": self.s.jev_model, "state": state, "questions": questions}
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.s.decide_timeout_s):
                resp = await self._client.post("/v1/systemone", json=body)
        except (TimeoutError, httpx.TimeoutException):
            raise self._fail("timeout") from None
        except httpx.HTTPError as exc:
            log.info("jev transport error: %s", type(exc).__name__)
            raise self._fail("upstream_error") from None
        latency_ms = (time.perf_counter() - started) * 1000

        status = resp.status_code
        if status in (401, 403):
            self._disable(status)
            raise DecideUnavailable("disabled")
        if status in (400, 422):
            raise DecideInvalid()
        if status != 200:
            log.info("jev status=%s", status)
            raise self._fail("rate_limited" if status in (429, 529) else "upstream_error")
        try:
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise ValueError
        except ValueError:
            raise self._fail("upstream_error") from None

        self.failures = 0
        data["latency_ms"] = round(latency_ms, 1)
        return data

    async def aclose(self) -> None:
        if self._warm_task and not self._warm_task.done():
            self._warm_task.cancel()
        await self._client.aclose()
