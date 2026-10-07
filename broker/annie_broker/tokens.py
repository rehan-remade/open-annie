"""Short-lived HMAC tokens binding a /decide caller to a minted voice session.

Format: ``<session_id>.<expires_unix>.<base64url(hmac_sha256)>``. Stateless, so a
broker restart with the same ANNIE_HMAC_SECRET keeps outstanding tokens valid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class TokenError(Exception):
    pass


def _sign(secret: bytes, session_id: str, expires_at: int) -> str:
    mac = hmac.new(secret, f"{session_id}.{expires_at}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()


def mint(secret: bytes, session_id: str, expires_at: int) -> str:
    # verify() splits from the right, so opaque ids containing '.' still round-trip.
    return f"{session_id}.{int(expires_at)}.{_sign(secret, session_id, int(expires_at))}"


def verify(secret: bytes, token: object, now: float | None = None) -> str:
    """Return the session id, or raise TokenError."""
    if not isinstance(token, str) or len(token) > 512:
        raise TokenError("malformed")
    parts = token.rsplit(".", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        raise TokenError("malformed")
    session_id, exp_s, sig = parts
    if not hmac.compare_digest(sig, _sign(secret, session_id, int(exp_s))):
        raise TokenError("bad_signature")
    if int(exp_s) <= (time.time() if now is None else now):
        raise TokenError("expired")
    return session_id
