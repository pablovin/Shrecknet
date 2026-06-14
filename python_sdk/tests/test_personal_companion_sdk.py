from __future__ import annotations

from datetime import datetime

from shrecknet_client.models import (
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
)


def test_personal_companion_models_parse() -> None:
    create = PersonalCompanionAgentCreate(name="Echo", writing_style="Warm", active=True)
    assert create.name == "Echo"

    update = PersonalCompanionAgentUpdate(writing_style="Calm")
    assert update.writing_style == "Calm"

    read = PersonalCompanionAgentRead(
        id="comp-1",
        user_id=1,
        name="Echo",
        avatar_url=None,
        writing_style="Warm",
        active=True,
        created_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
    )
    assert read.user_id == 1
    assert read.id == "comp-1"
