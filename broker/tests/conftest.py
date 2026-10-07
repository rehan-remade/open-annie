import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from annie_broker import Broker, Settings, create_app

OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
ANSWER = "v=0\r\no=- 9 9 IN IP4 10.0.0.1\r\ns=answer\r\n"
LEAK = "UPSTREAM_SECRET_DETAIL_do_not_forward"

JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {
        "face": {
            "type": "choice",
            "choice": "joyful",
            "probabilities": {"joyful": 0.9, "neutral": 0.1},
            "confidence": 0.85,
        },
        "wants_dance": {"type": "noul", "noul": 0.97},
    },
    "usage": {"input_tokens": 300, "output_tokens": 20},
}

QUESTIONS = {
    "face": {
        "type": "choice",
        "instructions": "Which face fits?",
        "criteria": {"joyful": "very happy", "neutral": None},
    }
}


class Upstream:
    """Scriptable httpx.MockTransport recording every request."""

    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self.handler = handler or (lambda req: httpx.Response(200, json=JEV_OK))
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def json(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)

    def calls(self, path: str) -> int:
        return sum(1 for r in self.requests if r.url.path == path)


def live_session_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/live/sessions":
        return httpx.Response(
            201, json={"session": {"id": "live_123"}, "transport": {"type": "webrtc", "sdp": ANSWER}}
        )
    return httpx.Response(404)


BASE = Settings(
    openai_api_key="sk-test",
    typesafe_api_key="ts-test",
    hmac_secret=b"test-secret",
    per_ip_daily=0,
    sideband=False,
    hangup_fallback=False,
)


@pytest.fixture
def make_client():
    clients = []

    def _make(settings: Settings | None = None, openai=None, typesafe=None, **overrides):
        s = replace(settings or BASE, **overrides)
        broker = Broker(
            s,
            openai_transport=(openai or Upstream(live_session_ok)).transport,
            typesafe_transport=(typesafe or Upstream()).transport,
        )
        client = TestClient(create_app(broker=broker))
        client.__enter__()
        clients.append((client, broker))
        return client

    yield _make
    for client, broker in clients:
        client.portal.call(broker.aclose)
        client.__exit__(None, None, None)
