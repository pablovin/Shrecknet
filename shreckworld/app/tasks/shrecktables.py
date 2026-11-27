"""Background calendar reconciliation. Celery beat is deployed separately from workers."""
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import or_, select
from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.shrecktables import TableSession, Table
from app.services.shrecktables_google import sync_event, import_session_artifacts, MEET_POLICY_VERSION

log = logging.getLogger(__name__)


@celery_app.task(name="shreckworld.shrecktables.sync", bind=True, max_retries=5)
def synchronize_session(self, session_id: int) -> str:
    with session_factory()() as db:
        result = sync_event(db, session_id)
        if result == "failed":
            raise self.retry(countdown=min(900, 30 * (2 ** self.request.retries)))
        return result


@celery_app.task(name="shreckworld.shrecktables.reconcile")
def reconcile_sessions() -> dict:
    """Recover missed Google events and import documents after conferences finish."""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=45)
    beginning = threshold - timedelta(days=29)
    with session_factory()() as db:
        pending = db.scalars(select(TableSession).join(Table, Table.id == TableSession.table_id).where(
            or_(TableSession.calendar_sync_status.in_(("pending", "failed", "partial")),
                (TableSession.meet_policy_version < MEET_POLICY_VERSION)
                & (TableSession.status == "scheduled")
                & (TableSession.scheduled_at_utc >= datetime.now(timezone.utc))
                & (TableSession.calendar_sync_status != "detached")
                & Table.calendar_id.is_not(None) & Table.google_connection_id.is_not(None)),
            TableSession.scheduled_at_utc.is_not(None),
        ).order_by(TableSession.id).limit(200)).all()
        for row in pending:
            try:
                sync_event(db, row.id)
            except Exception as exc:
                log.warning("Calendar reconciliation failed for session %s (%s)", row.id, type(exc).__name__)
        rows = db.scalars(select(TableSession).join(Table, Table.id == TableSession.table_id).where(
            TableSession.meet_space.is_not(None),
            TableSession.scheduled_at_utc >= beginning,
            TableSession.scheduled_at_utc <= threshold,
            Table.google_connection_id.is_not(None),
        ).order_by(TableSession.scheduled_at_utc.desc()).limit(200)).all()
        checked = imported = 0
        for row in rows:
            checked += 1
            try:
                imported += import_session_artifacts(db, row.id).get("imported", 0)
            except Exception as exc:
                log.warning("Meet document import deferred for session %s (%s)", row.id, type(exc).__name__)
        return {"calendar_checked": len(pending), "meet_checked": checked, "imported": imported}
