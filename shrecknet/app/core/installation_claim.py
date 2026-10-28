"""Optional protected bootstrap for the self-contained local distribution."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path

from app.core.runtime_secrets import read_runtime_secret


def claim_required() -> bool:
    return os.getenv("SHRECKNET_INSTALLATION_ENABLED", "false").lower() == "true"


def valid_claim(token: str | None) -> bool:
    expected = read_runtime_secret("installation_claim").strip()
    return bool(expected and token and hmac.compare_digest(expected, token))


@asynccontextmanager
async def registration_guard(name: str = "registration"):
    """Serialize bootstrap across API processes sharing the local data volume."""
    if not claim_required():
        yield
        return
    import fcntl
    directory = Path(os.getenv("SHRECKNET_RUNTIME_SECRETS_DIR", "/data/runtime-secrets"))
    with (directory / f".{name}.lock").open("a+b") as lock:
        os.chmod(lock.fileno(), 0o600)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
