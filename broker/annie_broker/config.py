"""Broker settings, read once from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field

VERSION = "0.1.0"

# GPT-Live bills $0.05/min, per second (docs/models/gpt-live-1, 2026-09-27).
VOICE_USD_PER_SECOND = 0.05 / 60
# A WebRTC create bills 15 s up front, credited against the running session.
VOICE_INIT_SECONDS = 15
# Jev: $0.042 per million input tokens, output free (docs.typesafe.ai/models).
JEV_USD_PER_INPUT_TOKEN = 0.042 / 1_000_000


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return (_env(name, "1" if default else "0") or "").lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in (_env(name, default) or "").split(",") if p.strip())


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = None
    typesafe_api_key: str | None = None
    hmac_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    daily_budget_usd: float = 25.0
    max_sessions: int = 20
    session_cap_s: int = 180
    idle_timeout_s: int = 30
    per_ip_daily: int = 2  # 0 disables the per-IP limit
    jev_model: str = "jev-latest"
    gptlive_model: str = "gpt-live-1"
    allowed_origins: tuple[str, ...] = ("http://localhost:*", "http://127.0.0.1:*")
    # Jev allows 1,200 rpm per key; stay under it so bursts never hit 429.
    jev_rpm: int = 1000
    jev_burst: int = 20
    # The browser enforces its own 900 ms (reply) / 700 ms (listen) budgets; this
    # server deadline only bounds how long an upstream call may hold a slot.
    decide_timeout_s: float = 1.5
    decide_per_session_rps: float = 5.0
    token_grace_s: int = 30
    default_voice: str = "marin"
    default_instructions: str = ""
    # Restrict what the browser data channel may send; () = upstream default (all).
    client_events: tuple[str, ...] = ()
    sideband: bool = True
    hangup_fallback: bool = True
    safety_identifier: bool = False
    ip_header: str | None = None  # e.g. "x-forwarded-for" behind a trusted proxy
    openai_base_url: str = "https://api.openai.com"
    typesafe_base_url: str = "https://api.typesafe.ai"
    # Dev only: GET /dev-token hands out /decide tokens without a voice session.
    dev: bool = False
    dev_token_ttl_s: int = 900

    @classmethod
    def from_env(cls) -> "Settings":
        secret = _env("ANNIE_HMAC_SECRET")
        return cls(
            # fal secrets are shared by every app on an account, so the fal deploy uses
            # namespaced ANNIE_* names; plain names still work locally.
            openai_api_key=_env("ANNIE_OPENAI_API_KEY") or _env("OPENAI_API_KEY"),
            typesafe_api_key=_env("ANNIE_TYPESAFE_API_KEY") or _env("TYPESAFE_API_KEY"),
            hmac_secret=secret.encode() if secret else secrets.token_bytes(32),
            daily_budget_usd=_float("ANNIE_DAILY_BUDGET_USD", 25.0),
            max_sessions=_int("ANNIE_MAX_SESSIONS", 20),
            session_cap_s=_int("ANNIE_SESSION_CAP_S", 180),
            idle_timeout_s=_int("ANNIE_IDLE_TIMEOUT_S", 30),
            per_ip_daily=_int("ANNIE_PER_IP_DAILY", 2),
            jev_model=_env("JEV_MODEL", "jev-latest"),
            gptlive_model=_env("GPTLIVE_MODEL", "gpt-live-1"),
            allowed_origins=_list(
                "ANNIE_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*"
            ),
            jev_rpm=_int("ANNIE_JEV_RPM", 1000),
            decide_timeout_s=_float("ANNIE_DECIDE_TIMEOUT_S", 1.5),
            default_voice=_env("ANNIE_VOICE", "marin"),
            default_instructions=_env("ANNIE_INSTRUCTIONS", ""),
            client_events=_list("ANNIE_CLIENT_EVENTS", ""),
            sideband=_bool("ANNIE_SIDEBAND", True),
            hangup_fallback=_bool("ANNIE_HANGUP_FALLBACK", True),
            safety_identifier=_bool("ANNIE_SAFETY_IDENTIFIER", False),
            ip_header=_env("ANNIE_IP_HEADER"),
            openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com"),
            typesafe_base_url=_env("TYPESAFE_BASE_URL", "https://api.typesafe.ai"),
            dev=_bool("ANNIE_DEV", False),
            dev_token_ttl_s=_int("ANNIE_DEV_TOKEN_TTL_S", 900),
        )

    @property
    def session_cap_usd(self) -> float:
        return self.session_cap_s * VOICE_USD_PER_SECOND
