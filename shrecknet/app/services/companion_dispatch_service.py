"""Recoverable SQL outbox dispatcher and retention; Redis is never authoritative."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, update, delete
from app.core.config_store import get_settings
from app.jobs.companion.runner import database
from app.models.character_companion import CompanionOutbox, CompanionTurn, CompanionEvent, CompanionAttachment, CompanionRapportAudit, utcnow
from app.repositories.character_companion_repository import CompanionRepository
from app.schemas.character_companion import DIMENSIONS, TERMINAL

logger = logging.getLogger(__name__)


async def interrupt_outstanding_model_work_on_startup():
    """Settle abandoned model work before the dispatcher can republish it.

    Privacy cleanup outbox entries remain pending and must still be dispatched.
    """
    async with database() as session:
        repo = CompanionRepository(session)
        rows = list((await session.execute(select(CompanionOutbox).where(
            CompanionOutbox.kind.in_(("turn", "rapport")),
            CompanionOutbox.status.in_(("pending", "running")),
        ))).scalars())
        rapport = []
        for row in rows:
            turn = await session.get(CompanionTurn, row.aggregate_id)
            if row.kind == "turn" and turn and turn.status not in TERMINAL:
                await repo.terminal(turn, "interrupted", error={"code": "server_restarted", "retryable": True})
            elif row.kind == "rapport" and turn and turn.rapport_status == "pending":
                rapport.append((turn, row))
            row.status = "done"
            row.lease = None
            row.updated_at = utcnow()

        # Settle each pair in completion order, without running a model. Advancing
        # the cursor is necessary for future completed exchanges to be evaluated.
        rapport.sort(key=lambda item: (item[0].user_id, item[0].character_agent_id, item[0].completion_sequence or 0))
        for turn, _ in rapport:
            pair = await repo.pair(turn.user_id, turn.character_agent_id)
            if turn.completion_sequence != pair.evaluated_sequence + 1:
                raise RuntimeError("Cannot settle companion rapport out of sequence on startup")
            scores = {key: getattr(pair, key) for key in DIMENSIONS}
            base_version = pair.version
            pair.version += 1
            pair.evaluated_sequence = turn.completion_sequence
            pair.interaction_count += 1
            pair.last_turn_id = turn.id
            pair.updated_at = utcnow()
            turn.rapport_status = "failed"
            session.add(CompanionRapportAudit(
                user_id=turn.user_id, character_agent_id=turn.character_agent_id,
                turn_id=turn.id, disposition="failed", data={"before": scores, "after": scores,
                    "base_version": base_version, "generation": pair.generation,
                    "completion_sequence": turn.completion_sequence, "proposal": None,
                    "reason": "server_restarted"}))
            await repo.event(turn.user_id, "rapport.updated", {
                "character_agent_id": turn.character_agent_id, "turn_id": turn.id,
                "version": pair.version, "generation": pair.generation, "scores": scores,
                "applied_deltas": {key: 0 for key in DIMENSIONS}, "disposition": "failed",
            }, turn_id=turn.id, chat_id=turn.chat_id)
        await session.commit()
        logger.info("Interrupted %s companion model outbox records on startup", len(rows))


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
