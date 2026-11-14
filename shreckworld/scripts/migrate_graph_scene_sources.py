"""Manually import graph-backed Sources and link Scenes/Milestones to them.

From shreckworld/: `python scripts/migrate_graph_scene_sources.py` previews the
current mapping. Add `--apply` to run the entity-to-Source importer first, then
write verified Source ID properties to Scenes and Milestones. DERIVED_FROM
relationships are always preserved. The final JSON is an audit report.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import fcntl
import json
import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import session_factory
from app.models.shreckworld import ShreckWorldLibraryItem
from app.services.graph_source_import import import_graph_sources
from app.services.scene_source_link_migration import migrate_scene_and_milestone_sources


def _source_snapshot(factory) -> dict[str, tuple[int | None, str]]:
    with factory() as db:
        return {
            row.id: (row.ontology_id, row.title)
            for row in db.scalars(select(ShreckWorldLibraryItem).where(
                ShreckWorldLibraryItem.source_type == "narrative",
            ))
        }


def _added_sources(
    before: dict[str, tuple[int | None, str]],
    factory,
    ontology_ids_all: list[int] | None = None,
) -> list[dict]:
    after = _source_snapshot(factory)
    added = defaultdict(list)
    for source_id in sorted(after.keys() - before.keys()):
        ontology_id, title = after[source_id]
        added[str(ontology_id) if ontology_id is not None else "unassigned"].append(
            {"source_id": source_id, "title": title}
        )
    ontology_keys = {str(ontology_id) if ontology_id is not None else "unassigned"
                     for ontology_id, _ in before.values()}
    ontology_keys.update(str(ontology_id) if ontology_id is not None else "unassigned"
                         for ontology_id, _ in after.values())
    ontology_keys.update(str(value) for value in (ontology_ids_all or []))
    ontology_keys.update(added)
    return [{"ontology_id": int(ontology) if ontology != "unassigned" else None,
             "sources": added.get(ontology, [])}
            for ontology in sorted(ontology_keys, key=lambda value: (value == "unassigned", value))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="run the entity-to-Source importer, then write Scene/Milestone Source references",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    settings.media_root.mkdir(parents=True, exist_ok=True)
    lock_path = settings.media_root / ".graph-scene-source-link-migration.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another graph Scene/Source migration is already running")
        try:
            factory = session_factory()
            before = _source_snapshot(factory)
            import_summary = {}
            if args.apply:
                import_summary = import_graph_sources(factory, settings, wait_for_lock=True)
            import_failed = bool(args.apply and import_summary.get("errors", 0))
            report = migrate_scene_and_milestone_sources(
                factory, settings, apply=args.apply and not import_failed,
                source_import_summary=import_summary,
            )
            report["sources_added"] = _added_sources(
                before, factory,
                [int(row["ontology_id"]) for row in report.get("ontologies", [])
                 if row.get("ontology_id") is not None],
            )
            if import_failed:
                report["apply_blocked"] = (
                    "The entity-to-Source importer reported errors; inspect its logs "
                    "and rerun after resolving them. No Scene/Milestone references were written."
                )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if report.get("apply_blocked") else 0
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
