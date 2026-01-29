from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.config_store import get_settings


def _chat_file_path(user_id: int, agent_id: str, chat_id: str) -> Path:
    settings = get_settings()
    base = Path(settings.media_root) / "chats" / str(user_id) / str(agent_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{chat_id}.json"


def init_chat_file(user_id: int, agent_id: str, chat_id: str) -> None:
    path = _chat_file_path(user_id, agent_id, chat_id)
    if path.exists():
        return
    data: dict[str, Any] = {
        "user_id": user_id,
        "agent_id": agent_id,
        "chat_id": chat_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "messages": [],
    }
    path.write_text(json_dumps(data))


def append_message(
    user_id: int,
    agent_id: str,
    chat_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    path = _chat_file_path(user_id, agent_id, chat_id)
    if not path.exists():
        init_chat_file(user_id, agent_id, chat_id)
    raw = path.read_text() if path.exists() else "{}"
    try:
        data = json_loads(raw)
    except Exception:
        data = {"messages": []}
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    if metadata:
        msg["meta"] = metadata
    data.setdefault("messages", []).append(msg)
    path.write_text(json_dumps(data))


def read_chat(user_id: int, agent_id: str, chat_id: str) -> dict[str, Any] | None:
    path = _chat_file_path(user_id, agent_id, chat_id)
    if not path.exists():
        return None
    try:
        return json_loads(path.read_text())
    except Exception:
        return None


def delete_chat_file(user_id: int, agent_id: str, chat_id: str) -> None:
    path = _chat_file_path(user_id, agent_id, chat_id)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def _json_default(obj: Any) -> Any:
    """Best-effort serializer to keep chat metadata JSON-friendly."""
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
    except Exception:
        pass

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)

    return str(obj)


def json_dumps(data: Any) -> str:
    import json as _json

    return _json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), default=_json_default
    )


def json_loads(raw: str) -> Any:
    import json as _json

    return _json.loads(raw)
