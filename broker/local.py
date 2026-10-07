"""Local dev runner for the broker on http://127.0.0.1:8787.

    python -m broker.local                      # from the repo root
    cd broker && .venv/bin/uvicorn local:app --port 8787 --reload

Reads KEY=VALUE lines from ../.env and ./.env (never overriding real env vars).
With no keys it still starts; /health then reports both providers false.
Local defaults: per-IP daily limit off, ANNIE_DEV=1 (GET /dev-token issues /decide
tokens without a voice session, for developing and recording the stage with real Jev).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")
os.environ.setdefault("ANNIE_PER_IP_DAILY", "0")
os.environ.setdefault("ANNIE_DEV", "1")  # enables GET /dev-token; never set on fal

from annie_broker import create_app  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines are noise
app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8787"))
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=port, log_level="info")


if __name__ == "__main__":
    main()
