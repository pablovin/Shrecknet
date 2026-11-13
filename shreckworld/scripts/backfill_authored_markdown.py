"""Recover pre-existing authored Sources created before physical Markdown files.

Dry run: python scripts/backfill_authored_markdown.py
Apply:   python scripts/backfill_authored_markdown.py --apply

This only repairs authored records without an available original file. Existing
SourceOrganization revisions are not rewritten; previous source IDs are retained.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from app.db.session import session_factory
from app.models import ShreckWorldLibraryItem
from app.services.source_workspace import (
    _write_authored_revision, get_organization, render_authored_markdown,
)


def backfill(*, apply: bool) -> tuple[int, int]:
    repaired = missing = 0
    with session_factory()() as db:
        rows = db.scalars(select(ShreckWorldLibraryItem).where(
            ShreckWorldLibraryItem.authored.is_(True),
        ).order_by(ShreckWorldLibraryItem.id)).all()
        for item in rows:
            current = item.storage_path or item.pdf_path
            if current and Path(current).is_file():
                continue
            missing += 1
            print(f"{'APPLY' if apply else 'DRY RUN'} authored={item.id} ontology={item.ontology_id} revision={item.source_revision}")
            if not apply:
                continue
            if item.ontology_id is None:
                print(f"SKIP {item.id}: no verified ontology owner")
                continue
            revision = get_organization(db, item)
            markdown = render_authored_markdown(revision.nodes)
            target = _write_authored_revision(item, markdown)
            try:
                item.storage_path = item.pdf_path = str(target)
                item.mime_type = 'text/markdown'
                item.file_size = len(markdown)
                item.source_sha256 = sha256(markdown).hexdigest()
                item.original_filename = item.original_filename if (item.original_filename or '').endswith('.md') else f"source-{item.id}.md"
                db.commit()
                repaired += 1
            except Exception:
                db.rollback()
                target.unlink(missing_ok=True)
                raise
    return missing, repaired


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Persist missing Markdown originals')
    args = parser.parse_args()
    missing, repaired = backfill(apply=args.apply)
    print(f"Found {missing} authored records without valid Markdown originals; repaired {repaired}.")


if __name__ == '__main__':
    main()
