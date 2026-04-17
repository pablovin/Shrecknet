"""Global maintenance mode flag used during destructive restore operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config_store import get_settings


class MaintenanceModeService:
    """Simple file-backed maintenance mode state."""

    def __init__(self) -> None:
        settings = get_settings()
        media_root = Path(settings.media_root)
        backup_dir = media_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = backup_dir / "maintenance_mode.json"

    def enable(self, reason: str) -> None:
        payload = {
            "active": True,
            "reason": reason,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def disable(self) -> None:
        if self.state_file.exists():
            self.state_file.unlink()

    def is_active(self) -> bool:
        return self.state_file.exists()
