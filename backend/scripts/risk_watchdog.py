"""GitHub runner probes the server over pinned SSH and sends only status changes."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.services.risk_watchdog import advance

REMOTE = """set -eu
container=$(docker ps --filter label=com.docker.compose.project=trading \
--filter label=com.docker.compose.service=api --format '{{.ID}}' | head -n 1)
test -n "$container"
docker exec "$container" /app/.venv/bin/python -c '
import json,time
from app.db import SessionLocal
from app.services.risk_monitor import health
db=SessionLocal()
print(json.dumps(health(db,time.time())))
db.close()'
"""


def probe() -> str:
    host, user = os.environ.get("DEPLOY_HOST", ""), os.environ.get("DEPLOY_USER", "")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or host.startswith("-"):
        return "down"
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", user) or user.startswith("-"):
        return "down"
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=15",
                "-i",
                str(Path.home() / ".ssh/deploy_key"),
                f"{user}@{host}",
                "bash -se",
            ],
            input=REMOTE,
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        )
        status = json.loads(result.stdout.strip().splitlines()[-1])
        if not status.get("healthy"):
            return "down"
        return "healthy" if status.get("coverage_complete") else "partial"
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return "down"


def main() -> int:
    # APP_ENV stays in runner memory; never print it or write it to the cache.
    config = dotenv_values(stream=io.StringIO(os.environ.get("APP_ENV", "")))
    key = os.environ.get("BARK_DEVICE_KEY") or config.get("BARK_DEVICE_KEY")
    base = os.environ.get("BARK_BASE_URL") or config.get("BARK_BASE_URL") or "https://api.day.app"
    if not key or not base.startswith("https://"):
        print("watchdog: Bark not configured with HTTPS; no push attempted")
        return 2

    def send(title, body):
        try:
            response = httpx.post(
                base.rstrip("/") + "/push",
                timeout=8,
                json={
                    "device_key": key,
                    "title": title,
                    "body": body,
                    "group": "portfolio-monitor-health",
                    "level": "timeSensitive",
                },
            )
            return response.is_success and response.json().get("code") == 200
        except Exception:
            return False

    path = Path(".watchdog/state.json")
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    status = probe()
    state = advance(previous, status, send)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    print(
        f"watchdog: {status}; consecutive failures={state['failures']}; alert acknowledged={bool(state.get('alerted'))}"
    )
    return 0 if status == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
