from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ShreckWorldSettings
from app.schemas.shreckworld import ShreckWorldModelTarget


def get_model_shreckworld_synthesizer(session: Session) -> ShreckWorldModelTarget:
    stored = session.get(ShreckWorldSettings, "default")
    if stored is not None:
        return ShreckWorldModelTarget.model_validate(stored.model_shreckworld_synthesizer)

    settings = get_settings()
    return ShreckWorldModelTarget(
        provider=settings.synthesis_provider,
        name=settings.synthesis_model,
    )


def save_model_shreckworld_synthesizer(
    session: Session, target: ShreckWorldModelTarget
) -> ShreckWorldModelTarget:
    stored = session.get(ShreckWorldSettings, "default")
    serialized = target.model_dump(mode="json")
    if stored is None:
        stored = ShreckWorldSettings(
            id="default",
            model_shreckworld_synthesizer=serialized,
        )
        session.add(stored)
    else:
        stored.model_shreckworld_synthesizer = serialized
    session.commit()
    return target
