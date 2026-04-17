from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.contracts.events import EventEnvelope
from app.core.config_store import get_settings

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(self, event: EventEnvelope) -> None: ...


class LoggingEventPublisher:
    async def publish(self, event: EventEnvelope) -> None:
        logger.info("event_published", extra={"event": event.model_dump(mode="json")})


class WebhookEventPublisher:
    def __init__(self, endpoint: str, timeout: float = 3.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    async def publish(self, event: EventEnvelope) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.endpoint, json=event.model_dump(mode="json"))
            response.raise_for_status()


def get_event_publisher() -> EventPublisher:
    settings = get_settings()
    mode = (settings.event_publisher_mode or "logging").strip().lower()
    if mode == "webhook":
        endpoint = (settings.event_webhook_url or "").strip()
        if endpoint:
            return WebhookEventPublisher(endpoint=endpoint)
    return LoggingEventPublisher()
