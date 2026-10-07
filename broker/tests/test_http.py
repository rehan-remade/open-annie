import time

import httpx

from annie_broker import tokens
from conftest import ANSWER, BASE, JEV_OK, LEAK, OFFER, QUESTIONS, Upstream, live_session_ok

ORIGIN = {"Origin": "http://localhost:5173"}


def token_for(session_id="live_123", ttl=60):
    return tokens.mint(BASE.hmac_secret, session_id, int(time.time()) + ttl)


def decide_body(**kw):
    return {"token": token_for(), "state": "Let's dance!", "questions": QUESTIONS, **kw}


# -- /health, /status --------------------------------------------------------


def test_health_without_keys(make_client):
    c = make_client(openai_api_key=None, typesafe_api_key=None)
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["version"]
    assert body["providers"] == {"voice": False, "decide": False}


def test_health_with_keys(make_client):
    assert make_client().get("/health").json()["providers"] == {"voice": True, "decide": True}


def test_status_shape(make_client):
    body = make_client().get("/status").json()
    assert set(body) == {
        "spent_today_usd", "budget_usd", "active_sessions", "max_sessions", "voice_seconds_today"
    }
    assert body["budget_usd"] == 25.0 and body["max_sessions"] == 20


# -- /decide -----------------------------------------------------------------


def test_decide_happy_path(make_client):
    jev = Upstream()
    c = make_client(typesafe=jev)
    r = c.post("/decide", json=decide_body(), headers=ORIGIN)
    assert r.status_code == 200
    body = r.json()
    assert body["answers"] == JEV_OK["answers"] and body["usage"] == JEV_OK["usage"]
    assert isinstance(body["latency_ms"], float)
    sent = jev.requests[-1]
    assert sent.url.path == "/v1/systemone"
    assert sent.headers["authorization"] == "Bearer ts-test"
    assert jev.json() == {"model": "jev-latest", "state": "Let's dance!", "questions": QUESTIONS}


def test_decide_without_key_is_503(make_client):
    r = make_client(typesafe_api_key=None).post("/decide", json=decide_body())
    assert r.status_code == 503 and r.json()["error"] == "decide_unavailable"


def test_decide_rejects_bad_and_expired_tokens(make_client):
    c = make_client()
    assert c.post("/decide", json=decide_body(token="live_123.1.bad")).status_code == 401
    expired = tokens.mint(BASE.hmac_secret, "live_123", int(time.time()) - 1)
    r = c.post("/decide", json=decide_body(token=expired))
    assert r.status_code == 401 and r.json() == {"error": "invalid_token"}


def test_decide_validates_input(make_client):
    c = make_client()
    assert c.post("/decide", json={"token": token_for(), "state": "x"}).status_code == 400
    assert c.post("/decide", content=b"{not json").status_code == 400
    assert c.post("/decide", content=b"x" * (70 * 1024)).status_code == 413


def test_decide_timeout_opens_breaker_after_three(make_client):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    jev = Upstream(slow)
    c = make_client(typesafe=jev)
    for _ in range(3):
        r = c.post("/decide", json=decide_body())
        assert r.status_code == 503
        assert r.json() == {"error": "decide_unavailable", "reason": "timeout"}
    r = c.post("/decide", json=decide_body())
    assert r.json()["reason"] == "breaker_open"
    assert jev.calls("/v1/systemone") == 3  # the open breaker never reached upstream


def test_decide_total_deadline(make_client):
    import asyncio

    async def hang(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=JEV_OK)

    jev = Upstream()
    jev.transport = httpx.MockTransport(hang)
    c = make_client(typesafe=jev, decide_timeout_s=0.05)
    t0 = time.perf_counter()
    r = c.post("/decide", json=decide_body())
    assert r.status_code == 503 and r.json()["reason"] == "timeout"
    assert time.perf_counter() - t0 < 1.0


def test_governor_sheds_load(make_client):
    c = make_client(jev_rpm=1, jev_burst=1)
    assert c.post("/decide", json=decide_body()).status_code == 200
    r = c.post("/decide", json=decide_body())
    assert r.status_code == 503 and r.json()["reason"] == "rate_limited"


def test_per_session_rate_limit(make_client):
    c = make_client(decide_per_session_rps=0.5)  # burst of 1
    assert c.post("/decide", json=decide_body()).status_code == 200
    assert c.post("/decide", json=decide_body()).json()["reason"] == "session_rate_limited"


def test_upstream_auth_failure_disables_decide(make_client):
    jev = Upstream(lambda req: httpx.Response(401, json={"detail": LEAK}))
    c = make_client(typesafe=jev)
    r = c.post("/decide", json=decide_body())
    assert r.status_code == 503 and LEAK not in r.text
    assert c.get("/health").json()["providers"]["decide"] is False


def test_upstream_error_bodies_not_leaked(make_client):
    for status in (500, 422, 429, 529):
        jev = Upstream(lambda req, s=status: httpx.Response(s, json={"detail": LEAK}))
        r = make_client(typesafe=jev).post("/decide", json=decide_body())
        assert r.status_code in (503, 422)
        assert LEAK not in r.text


def test_decide_counts_jev_spend(make_client):
    c = make_client()
    c.post("/decide", json=decide_body())
    assert c.get("/status").json()["spent_today_usd"] > 0


def test_decide_origin_check(make_client):
    r = make_client().post("/decide", json=decide_body(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# -- WS /decide ----------------------------------------------------------------


def test_ws_decide_echoes_ids(make_client):
    c = make_client()
    with c.websocket_connect("/decide", headers=ORIGIN) as ws:
        ws.send_json({"id": "a", **decide_body()})
        ws.send_json({"id": 2, **decide_body(token="nope.1.x")})
        replies = {m["id"]: m for m in (ws.receive_json(), ws.receive_json())}
    assert replies["a"]["answers"] == JEV_OK["answers"] and "latency_ms" in replies["a"]
    assert replies[2] == {"id": 2, "error": "invalid_token"}


def test_ws_decide_bad_json(make_client):
    with make_client().websocket_connect("/decide") as ws:
        ws.send_text("{nope")
        assert ws.receive_json() == {"id": None, "error": "invalid_request"}


# -- /session ------------------------------------------------------------------


def test_session_mints_via_sdp_exchange(make_client):
    oai = Upstream(live_session_ok)
    c = make_client(openai=oai)
    before = int(time.time())
    r = c.post("/session", json={"sdp": OFFER, "voice": "cedar", "instructions": "Be Annie."}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sdp"] == ANSWER and body["session_id"] == "live_123"
    assert before + 180 <= body["expires_at"] <= int(time.time()) + 180
    assert tokens.verify(BASE.hmac_secret, body["token"]) == "live_123"

    sent = oai.requests[0]
    assert sent.method == "POST" and sent.url.path == "/v1/live/sessions"
    assert sent.headers["authorization"] == "Bearer sk-test"
    assert sent.headers["content-type"] == "application/json"
    assert oai.json(0) == {
        "session": {
            "model": "gpt-live-1",
            "audio": {"output": {"voice": "cedar"}},
            "instructions": "Be Annie.",
        },
        "transport": {"type": "webrtc", "sdp": OFFER},
    }
    assert c.get("/status").json()["active_sessions"] == 1
    # The minted token unlocks /decide.
    assert c.post("/decide", json=decide_body(token=body["token"])).status_code == 200


def test_session_restricts_client_events_when_configured(make_client):
    oai = Upstream(live_session_ok)
    make_client(openai=oai, client_events=("session.close",)).post("/session", json={"sdp": OFFER})
    assert oai.json(0)["session"]["client"] == {
        "data_channel": {"allowed_client_events": ["session.close"]}
    }


def test_session_without_key_is_503(make_client):
    r = make_client(openai_api_key=None).post("/session", json={"sdp": OFFER})
    assert r.status_code == 503 and r.json() == {"error": "voice_unavailable"}


def test_session_validates_input(make_client):
    c = make_client()
    assert c.post("/session", json={}).status_code == 400
    assert c.post("/session", json={"sdp": OFFER, "voice": "Bad Voice!"}).status_code == 400
    assert c.post("/session", json={"sdp": OFFER}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_session_upstream_error_not_leaked_and_slot_released(make_client):
    oai = Upstream(lambda req: httpx.Response(500, json={"error": {"message": LEAK}}))
    c = make_client(openai=oai, max_sessions=1)
    for _ in range(2):  # a failed mint must not hold the only slot
        r = c.post("/session", json={"sdp": OFFER})
        assert r.status_code == 502 and r.json() == {"error": "voice_upstream_error"}
        assert LEAK not in r.text
    assert c.get("/status").json()["active_sessions"] == 0


def test_session_upstream_rate_limit_is_503(make_client):
    oai = Upstream(lambda req: httpx.Response(429, text=LEAK))
    r = make_client(openai=oai).post("/session", json={"sdp": OFFER})
    assert r.status_code == 503 and r.json() == {"error": "voice_busy"}


def test_session_empty_openai_balance_is_named(make_client):
    body = {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": LEAK}}
    oai = Upstream(lambda req: httpx.Response(429, json=body))
    r = make_client(openai=oai).post("/session", json={"sdp": OFFER})
    assert r.status_code == 503 and r.json() == {"error": "voice_no_credit"}
    assert LEAK not in r.text


def test_budget_exhaustion_rejects_session(make_client):
    # One 180 s session reserves $0.15; a $0.20 budget admits exactly one.
    c = make_client(daily_budget_usd=0.20)
    assert c.post("/session", json={"sdp": OFFER}).status_code == 200
    r = c.post("/session", json={"sdp": OFFER})
    assert r.status_code == 429 and r.json() == {"error": "budget_exhausted"}


def test_max_sessions(make_client):
    c = make_client(max_sessions=1)
    assert c.post("/session", json={"sdp": OFFER}).status_code == 200
    assert c.post("/session", json={"sdp": OFFER}).json() == {"error": "at_capacity"}


def test_per_ip_limit(make_client):
    c = make_client(per_ip_daily=2, ip_header="x-forwarded-for")
    a = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    assert c.post("/session", json={"sdp": OFFER}, headers=a).status_code == 200
    assert c.post("/session", json={"sdp": OFFER}, headers=a).status_code == 200
    r = c.post("/session", json={"sdp": OFFER}, headers=a)
    assert r.status_code == 429 and r.json() == {"error": "ip_limit"}
    other = {"X-Forwarded-For": "198.51.100.9"}
    assert c.post("/session", json={"sdp": OFFER}, headers=other).status_code == 200


# -- dev token + CORS ----------------------------------------------------------


def test_dev_token_is_404_unless_dev(make_client):
    assert make_client().get("/dev-token").status_code == 404


def test_dev_token_unlocks_decide(make_client):
    c = make_client(dev=True)
    body = c.get("/dev-token").json()
    assert set(body) == {"token", "expires_at"} and body["expires_at"] > time.time()
    r = c.post("/decide", json=decide_body(token=body["token"]), headers={"Origin": "http://127.0.0.1:8080"})
    assert r.status_code == 200 and set(r.json()) >= {"answers", "usage", "latency_ms"}


def test_cors_preflight(make_client):
    c = make_client()
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    ok = c.options("/decide", headers={"Origin": "http://127.0.0.1:8080", **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    bad = c.options("/decide", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers
