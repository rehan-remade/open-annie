"""The broker contract (docs/contracts.md) as framework-light handlers plus a FastAPI app.

`Broker` holds all state and returns (status, body) pairs, so the FastAPI app
here and the fal App in fal_app.py share every line of logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager, suppress
from typing import Any, Callable, Optional, Union

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import tokens
from .budget import Budget, Rejected
from .config import VERSION, Settings
from .decide import DecideClient, DecideInvalid, DecideUnavailable
from .reaper import Reaper
from .voice import VoiceClient, VoiceError

log = logging.getLogger("annie")

MAX_BODY_BYTES = 64 * 1024
WS_MAX_INFLIGHT = 4


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sdp: str = Field(min_length=10, max_length=20_000)
    voice: Optional[str] = Field(default=None, max_length=32, pattern=r"^[a-z0-9_-]+$")
    instructions: Optional[str] = Field(default=None, max_length=16_000)


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(max_length=512)
    state: Union[str, dict, list]
    questions: dict[str, dict] = Field(min_length=1, max_length=64)


def _err(code: str, **extra: Any) -> dict:
    return {"error": code, **extra}


def origin_matcher(patterns: tuple[str, ...]) -> Optional[str]:
    """Regex for allowed origins; None means allow any. `*` inside a pattern matches a port/host part."""
    if not patterns or "*" in patterns:
        return None
    return "^(" + "|".join(re.escape(p).replace(r"\*", "[^/]*") for p in patterns) + ")$"


class Broker:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        openai_transport: Optional[httpx.AsyncBaseTransport] = None,
        typesafe_transport: Optional[httpx.AsyncBaseTransport] = None,
        sideband_connect: Optional[Callable] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.s = settings or Settings.from_env()
        self.clock = clock
        self.budget = Budget(self.s, clock)
        self.voice = VoiceClient(self.s, openai_transport)
        self.decider = DecideClient(self.s, typesafe_transport)
        self.reaper = Reaper(self.s, self.budget, self.voice, sideband_connect, clock)
        self.origin_regex = origin_matcher(self.s.allowed_origins)
        self._origin_re = re.compile(self.origin_regex) if self.origin_regex else None

    async def start(self) -> None:
        self.decider.start_warm()

    async def aclose(self) -> None:
        await self.reaper.aclose()
        await self.voice.aclose()
        await self.decider.aclose()

    # -- request context -----------------------------------------------------

    def origin_allowed(self, origin: Optional[str]) -> bool:
        # Non-browser callers send no Origin; the budget and per-IP limits still apply.
        if origin is None or self._origin_re is None:
            return True
        return bool(self._origin_re.match(origin))

    def client_ip(self, headers: Any, peer: Optional[str]) -> str:
        if self.s.ip_header and (value := headers.get(self.s.ip_header)):
            return value.split(",")[0].strip() or "unknown"
        return peer or "unknown"

    # -- routes ----------------------------------------------------------------

    def health(self) -> dict:
        return {
            "ok": True,
            "version": VERSION,
            "providers": {"voice": self.voice.available, "decide": self.decider.available},
        }

    def status(self) -> dict:
        return self.budget.status()

    def dev_token(self) -> tuple[int, dict]:
        if not self.s.dev:
            return 404, {"detail": "Not Found"}
        expires_at = int(self.clock()) + self.s.dev_token_ttl_s
        session_id = f"dev_{secrets.token_hex(6)}"
        return 200, {"token": tokens.mint(self.s.hmac_secret, session_id, expires_at),
                     "expires_at": expires_at}

    async def session(self, raw: bytes, ip: str, origin: Optional[str]) -> tuple[int, dict]:
        if not self.origin_allowed(origin):
            return 403, _err("origin_not_allowed")
        if not self.voice.available:
            return 503, _err("voice_unavailable")
        try:
            req = SessionRequest.model_validate_json(raw)
        except ValidationError:
            return 400, _err("invalid_request")
        try:
            reservation = self.budget.admit(ip)
        except Rejected as rej:
            return 429, _err(rej.code)

        safety_id = self.budget.ip_key(ip) if self.s.safety_identifier else None
        try:
            minted = await self.voice.create(req.sdp, req.voice, req.instructions, safety_id)
        except VoiceError as exc:
            self.budget.cancel(reservation)
            return (503 if exc.code in ("voice_busy", "voice_no_credit") else 502), _err(exc.code)
        except BaseException:
            self.budget.cancel(reservation)
            raise

        self.budget.open(reservation, minted.session_id)
        self.reaper.watch(minted.session_id)
        cap_deadline = int(self.clock()) + self.s.session_cap_s
        expires_at = min(minted.expires_at, cap_deadline) if minted.expires_at else cap_deadline
        token = tokens.mint(self.s.hmac_secret, minted.session_id, expires_at + self.s.token_grace_s)
        return 200, {
            "sdp": minted.sdp,
            "session_id": minted.session_id,
            "expires_at": expires_at,
            "token": token,
        }

    async def decide(self, payload: Any) -> tuple[int, dict]:
        try:
            req = DecideRequest.model_validate(payload)
        except ValidationError:
            return 400, _err("invalid_request")
        try:
            session_id = tokens.verify(self.s.hmac_secret, req.token, self.clock())
        except tokens.TokenError:
            return 401, _err("invalid_token")
        try:
            body = await self.decider.decide(req.state, req.questions, session_key=session_id)
        except DecideUnavailable as exc:
            return 503, _err("decide_unavailable", reason=exc.reason)
        except DecideInvalid:
            return 422, _err("decide_invalid")
        usage = body.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
            self.budget.add_jev(usage["input_tokens"])
        return 200, body

    async def decide_raw(self, raw: Union[bytes, str], origin: Optional[str]) -> tuple[int, dict]:
        if not self.origin_allowed(origin):
            return 403, _err("origin_not_allowed")
        if len(raw) > MAX_BODY_BYTES:
            return 413, _err("too_large")
        try:
            payload = json.loads(raw)
        except ValueError:
            return 400, _err("invalid_request")
        return await self.decide(payload)

    async def decide_socket(self, ws: WebSocket) -> None:
        """WS /decide: one JSON request per message, replies echo `id`, answered out of order."""
        if not self.origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept()
        send_lock = asyncio.Lock()
        inflight = asyncio.Semaphore(WS_MAX_INFLIGHT)
        tasks: set[asyncio.Task] = set()

        async def reply(msg_id: Any, body: dict) -> None:
            async with send_lock:
                with suppress(Exception):
                    await ws.send_text(json.dumps({"id": msg_id, **body}))

        async def handle(msg_id: Any, payload: Any) -> None:
            try:
                _, body = await self.decide(payload)
                await reply(msg_id, body)
            finally:
                inflight.release()

        try:
            while True:
                raw = await ws.receive_text()
                msg_id = None
                if len(raw) > MAX_BODY_BYTES:
                    await reply(None, _err("too_large"))
                    continue
                try:
                    payload = json.loads(raw)
                    msg_id = payload.get("id") if isinstance(payload, dict) else None
                except ValueError:
                    await reply(None, _err("invalid_request"))
                    continue
                if inflight.locked():
                    await reply(msg_id, _err("decide_unavailable", reason="too_many_inflight"))
                    continue
                await inflight.acquire()
                task = asyncio.ensure_future(handle(msg_id, payload))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (WebSocketDisconnect, RuntimeError, KeyError):  # KeyError: binary frame
            pass
        finally:
            for task in tasks:
                task.cancel()


async def read_limited(request: Request, limit: int = MAX_BODY_BYTES) -> Optional[bytes]:
    """Read the body, giving up (None) past `limit` bytes instead of buffering it all."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(settings: Optional[Settings] = None, broker: Optional[Broker] = None) -> FastAPI:
    owned = broker is None
    broker = broker or Broker(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await broker.start()
        try:
            yield
        finally:
            if owned:
                await broker.aclose()

    app = FastAPI(title="annie-broker", version=VERSION, lifespan=lifespan)
    app.state.broker = broker
    if broker.origin_regex is None:
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                           allow_headers=["content-type"], max_age=600)
    else:
        app.add_middleware(CORSMiddleware, allow_origin_regex=broker.origin_regex,
                           allow_methods=["GET", "POST"], allow_headers=["content-type"], max_age=600)

    def respond(result: tuple[int, dict]) -> JSONResponse:
        return JSONResponse(result[1], status_code=result[0])

    @app.get("/health")
    async def health() -> dict:
        return broker.health()

    @app.get("/status")
    async def status() -> dict:
        return broker.status()

    @app.get("/dev-token")
    async def dev_token() -> JSONResponse:
        return respond(broker.dev_token())

    @app.post("/session")
    async def session(request: Request) -> JSONResponse:
        raw = await read_limited(request)
        if raw is None:
            return respond((413, _err("too_large")))
        ip = broker.client_ip(request.headers, request.client.host if request.client else None)
        return respond(await broker.session(raw, ip, request.headers.get("origin")))

    @app.post("/decide")
    async def decide(request: Request) -> JSONResponse:
        raw = await read_limited(request)
        if raw is None:
            return respond((413, _err("too_large")))
        return respond(await broker.decide_raw(raw, request.headers.get("origin")))

    @app.websocket("/decide")
    async def decide_ws(websocket: WebSocket) -> None:
        await broker.decide_socket(websocket)

    return app
