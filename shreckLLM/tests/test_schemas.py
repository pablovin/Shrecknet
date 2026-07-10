from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ChatMessage, ChatRequest


def test_chat_message_validation() -> None:
    msg = ChatMessage(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_chat_message_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="")


def test_chat_request_requires_messages() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(provider_id="ollama", model="gemma3:4b", messages=[])


def test_chat_request_requires_provider_id() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(model="gemma3:4b", messages=[{"role": "user", "content": "hi"}])


def test_chat_request_requires_model() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(provider_id="ollama", messages=[{"role": "user", "content": "hi"}])
