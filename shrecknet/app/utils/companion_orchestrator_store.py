"""Filesystem store for world-scoped companion orchestrator sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config_store import get_settings


def _sessions_base_path() -> Path:
    settings = get_settings()
    base = Path(settings.media_root) / "companion_orchestrator" / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _chats_base_path() -> Path:
    settings = get_settings()
    base = Path(settings.media_root) / "companion_orchestrator" / "chats"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _session_path(user_id: int, session_id: str) -> Path:
    user_root = _sessions_base_path() / str(user_id)
    user_root.mkdir(parents=True, exist_ok=True)
    return user_root / f"{session_id}.json"


def _list_user_session_paths(user_id: int) -> list[Path]:
    user_root = _sessions_base_path() / str(user_id)
    if not user_root.exists():
        return []
    return sorted(path for path in user_root.glob("*.json") if path.is_file())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chat_file_path(user_id: int, companion_id: str, session_id: str) -> Path:
    user_root = _chats_base_path() / str(user_id) / str(companion_id)
    user_root.mkdir(parents=True, exist_ok=True)
    return user_root / f"{session_id}.json"


def _json_default(obj: Any) -> Any:
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
    except Exception:
        pass

    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)

    return str(obj)


def _json_dumps(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_loads(raw: str) -> Any:
    return json.loads(raw)


def init_chat_file(user_id: int, companion_id: str, session_id: str) -> None:
    path = _chat_file_path(user_id, companion_id, session_id)
    if path.exists():
        return
    payload: dict[str, Any] = {
        "user_id": user_id,
        "companion_id": companion_id,
        "session_id": session_id,
        "created_at": _utc_now_iso(),
        "messages": [],
    }
    path.write_text(_json_dumps(payload))


def reset_chat_file(user_id: int, companion_id: str, session_id: str) -> None:
    path = _chat_file_path(user_id, companion_id, session_id)
    payload: dict[str, Any] = {
        "user_id": user_id,
        "companion_id": companion_id,
        "session_id": session_id,
        "created_at": _utc_now_iso(),
        "messages": [],
        "updated_at": _utc_now_iso(),
    }
    path.write_text(_json_dumps(payload))


def append_chat_message(
    user_id: int,
    companion_id: str,
    session_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    path = _chat_file_path(user_id, companion_id, session_id)
    if not path.exists():
        init_chat_file(user_id, companion_id, session_id)
    raw = path.read_text() if path.exists() else "{}"
    try:
        payload = _json_loads(raw)
    except Exception:
        payload = {"messages": []}

    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "ts": _utc_now_iso(),
    }
    if metadata:
        msg["meta"] = metadata

    payload.setdefault("messages", []).append(msg)
    payload["updated_at"] = _utc_now_iso()
    path.write_text(_json_dumps(payload))


def read_chat_file(
    user_id: int,
    companion_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    path = _chat_file_path(user_id, companion_id, session_id)
    if not path.exists():
        return None
    try:
        loaded = _json_loads(path.read_text())
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def create_session(
    *,
    user_id: int,
    companion_id: str,
    ontology_id: int,
    allocated_tools: dict[str, Any],
) -> dict[str, Any]:
    """Create and persist a world-scoped orchestrator session."""
    session_id = str(uuid4())
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "companion_id": companion_id,
        "ontology_id": int(ontology_id),
        "allocated_tools": allocated_tools,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }
    _session_path(user_id, session_id).write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    )
    init_chat_file(user_id, companion_id, session_id)
    return payload


def create_or_update_session(
    *,
    user_id: int,
    companion_id: str,
    ontology_id: int,
    allocated_tools: dict[str, Any],
) -> dict[str, Any]:
    """Create a new session or update existing one for the same companion."""
    existing_payload: dict[str, Any] | None = None
    existing_path: Path | None = None

    for path in _list_user_session_paths(user_id):
        try:
            loaded = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(loaded, dict):
            continue
        if str(loaded.get("companion_id")) == str(companion_id):
            existing_payload = loaded
            existing_path = path
            break

    if existing_payload is None or existing_path is None:
        return create_session(
            user_id=user_id,
            companion_id=companion_id,
            ontology_id=ontology_id,
            allocated_tools=allocated_tools,
        )

    previous_ontology = int(existing_payload.get("ontology_id") or 0)
    session_id = str(existing_payload.get("session_id") or existing_path.stem)

    updated_payload: dict[str, Any] = {
        **existing_payload,
        "session_id": session_id,
        "user_id": user_id,
        "companion_id": companion_id,
        "ontology_id": int(ontology_id),
        "allocated_tools": allocated_tools,
        "updated_at": _utc_now_iso(),
    }
    if not updated_payload.get("created_at"):
        updated_payload["created_at"] = _utc_now_iso()

    _session_path(user_id, session_id).write_text(
        json.dumps(updated_payload, ensure_ascii=True, separators=(",", ":"))
    )

    if previous_ontology != int(ontology_id):
        reset_chat_file(user_id, companion_id, session_id)
    else:
        init_chat_file(user_id, companion_id, session_id)

    return updated_payload


def get_session(user_id: int, session_id: str) -> dict[str, Any] | None:
    """Load a persisted session for a user."""
    path = _session_path(user_id, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def update_session_allocated_tools(
    user_id: int,
    session_id: str,
    allocated_tools: dict[str, Any],
) -> dict[str, Any] | None:
    """Update allocated tools for an existing session."""
    payload = get_session(user_id, session_id)
    if payload is None:
        return None

    updated_payload: dict[str, Any] = {
        **payload,
        "allocated_tools": allocated_tools,
        "updated_at": _utc_now_iso(),
    }
    _session_path(user_id, session_id).write_text(
        json.dumps(updated_payload, ensure_ascii=True, separators=(",", ":"))
    )
    return updated_payload
