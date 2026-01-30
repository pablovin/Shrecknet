from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from openai import (
    APIConnectionError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
    AsyncOpenAI,
)

from app.core.config_store import Settings, is_openai_configured

_CACHE_TTL_SECONDS = 60
_cached_status: dict[str, Any] | None = None
_cached_at: datetime | None = None
_cache_lock = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_cache_valid(now: datetime) -> bool:
    if _cached_at is None or _cached_status is None:
        return False
    return now - _cached_at < timedelta(seconds=_CACHE_TTL_SECONDS)


def _base_status(settings: Settings) -> dict[str, Any]:
    configured = is_openai_configured(settings)
    return {
        "configured": configured,
        "valid": None,
        "error": None,
    }


async def get_openai_status(settings: Settings) -> dict[str, Any]:
    now = _utc_now()
    if _is_cache_valid(now):
        return dict(_cached_status)  # defensive copy

    async with _cache_lock:
        now = _utc_now()
        if _is_cache_valid(now):
            return dict(_cached_status)

        status = _base_status(settings)
        if not status["configured"]:
            _set_cache(status, now)
            return dict(status)

        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=10,
            max_retries=0,
        )
        try:
            await client.models.list()
            status["valid"] = True
        except AuthenticationError:
            status["valid"] = False
            status["error"] = "invalid_api_key"
        except RateLimitError:
            status["valid"] = True
            status["error"] = "rate_limited"
        except APIConnectionError:
            status["valid"] = None
            status["error"] = "connection_error"
        except OpenAIError:
            status["valid"] = None
            status["error"] = "openai_error"
        except Exception:
            status["valid"] = None
            status["error"] = "unknown_error"
        finally:
            close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close_fn is not None:
                maybe_coro = close_fn()
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro

        _set_cache(status, now)
        return dict(status)


def _set_cache(status: dict[str, Any], now: datetime) -> None:
    global _cached_status, _cached_at
    _cached_status = dict(status)
    _cached_at = now
