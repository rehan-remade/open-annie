"""GPT-Live session minting: exchange the browser's SDP offer for an answer.

POST /v1/live/sessions with JSON {session, transport:{type:"webrtc", sdp}}; the
201 response is {session:{id}, transport:{type:"webrtc", sdp:<answer>}}. The
HTTP request itself starts (and pre-bills 15 s of) the session. See UPSTREAM.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import Settings

log = logging.getLogger("annie.voice")


class VoiceError(Exception):
    def __init__(self, code: str, upstream_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class VoiceSession:
    session_id: str
    sdp: str
    expires_at: int | None  # only present if upstream includes it in the create response


class VoiceClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=transport,
        )

    @property
    def available(self) -> bool:
        return bool(self.s.openai_api_key)

    def _session_config(self, voice: str | None, instructions: str | None) -> dict:
        session: dict = {
            "model": self.s.gptlive_model,
            "audio": {"output": {"voice": voice or self.s.default_voice}},
        }
        text = instructions or self.s.default_instructions
        if text:
            session["instructions"] = text
        if self.s.client_events:
            events = "all" if self.s.client_events == ("all",) else list(self.s.client_events)
            session["client"] = {"data_channel": {"allowed_client_events": events}}
        # No `delegation`: omitted selects client delegation; Annie has no backend agent.
        return session

    async def create(
        self,
        sdp: str,
        voice: str | None = None,
        instructions: str | None = None,
        safety_id: str | None = None,
    ) -> VoiceSession:
        if not self.available:
            raise VoiceError("voice_unavailable")
        headers = {"OpenAI-Safety-Identifier": safety_id} if safety_id else None
        body = {
            "session": self._session_config(voice, instructions),
            "transport": {"type": "webrtc", "sdp": sdp},
        }
        try:
            resp = await self._client.post("/v1/live/sessions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("gpt-live create transport error: %s", type(exc).__name__)
            raise VoiceError("voice_upstream_error") from None
        if resp.status_code not in (200, 201):
            # Status only: upstream bodies may echo our config and are never forwarded.
            # Only the error *type* is read, to tell an empty balance from a concurrency cap;
            # the body itself is never forwarded or logged.
            try:
                etype = resp.json().get("error", {}).get("type")
            except Exception:
                etype = None
            log.warning("gpt-live create failed: status=%s type=%s", resp.status_code, etype)
            if resp.status_code == 429 and etype == "insufficient_quota":
                code = "voice_no_credit"
            else:
                code = "voice_busy" if resp.status_code == 429 else "voice_upstream_error"
            raise VoiceError(code, resp.status_code)
        try:
            data = resp.json()
            session_id = data["session"]["id"]
            answer = data["transport"]["sdp"]
            expires_at = data["session"].get("expires_at")
        except (ValueError, KeyError, TypeError):
            log.warning("gpt-live create: unexpected response shape")
            raise VoiceError("voice_upstream_error", resp.status_code) from None
        if not isinstance(session_id, str) or not isinstance(answer, str):
            raise VoiceError("voice_upstream_error", resp.status_code)
        return VoiceSession(session_id, answer, int(expires_at) if expires_at else None)

    async def hangup(self, session_id: str) -> bool:
        """Best-effort server-side end. Documented for SIP sessions; unverified for WebRTC."""
        try:
            resp = await self._client.post(f"/v1/live/sessions/{session_id}/hangup")
            return resp.status_code < 300
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
