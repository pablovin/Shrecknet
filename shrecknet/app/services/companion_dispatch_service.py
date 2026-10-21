"""Recoverable SQL outbox dispatcher and retention; Redis is never authoritative."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, update, delete
from app.core.config_store import get_settings
from app.jobs.companion.runner import database
from app.models.character_companion import CompanionOutbox, CompanionTurn, CompanionEvent, CompanionAttachment, utcnow
from app.repositories.character_companion_repository import CompanionRepository
from app.schemas.character_companion import TERMINAL

logger = logging.getLogger(__name__)


async def dispatch_once():
    from app.tasks.character_companion import process_companion_outbox
    s = get_settings()
    async with database() as session:
        stale = utcnow() - timedelta(seconds=s.companion_turn_timeout_s + 30)
        rows = list((await session.execute(select(CompanionOutbox).where(CompanionOutbox.status == "running", CompanionOutbox.updated_at < stale))).scalars())
        for row in rows:
            if row.kind == "turn":
                turn = await session.get(CompanionTurn, row.aggregate_id)
                if turn and turn.status not in TERMINAL:
                    await CompanionRepository(session).terminal(turn, "interrupted", error={"code": "worker_interrupted", "retryable": True}, lease=row.lease)
                row.status = "done"
            else:
                row.status = "pending"
        # Expiration leaves final snapshots intact; no event replay invokes models.
        await session.execute(delete(CompanionEvent).where(CompanionEvent.created_at < utcnow()-timedelta(days=s.companion_event_retention_days)))
        attachments = list((await session.execute(select(CompanionAttachment).where(CompanionAttachment.turn_id.is_(None), CompanionAttachment.created_at < utcnow()-timedelta(hours=24), CompanionAttachment.deleted.is_(False)))).scalars())
        if attachments:
            from app.services.companion_attachment_service import private_root
            for row in attachments:
                (private_root()/row.storage_key).unlink(missing_ok=True)
                row.deleted = True
        pending = list((await session.execute(select(CompanionOutbox.id, CompanionOutbox.kind).where(CompanionOutbox.status == "pending").order_by(CompanionOutbox.updated_at).limit(100))).all())
        await session.commit()
    for identifier, kind in pending:
        # Failed publishing leaves pending SQL intact. Worker CAS handles duplicates.
        await asyncio.to_thread(process_companion_outbox.apply_async, args=[identifier], queue="companion_rapport" if kind == "rapport" else "companion")


async def dispatch_loop():
    while True:
        try:
            await dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("companion_dispatch_unavailable", exc_info=True)
        await asyncio.sleep(5)


async def dispatch_after_admission():
    """Best-effort immediate delivery; durable polling recovers broker failures."""
    try:
        await dispatch_once()
    except Exception:
        logger.warning("companion_immediate_dispatch_unavailable")
