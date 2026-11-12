"""Idempotently backfill Book Maps from persisted document structure (no embeddings)."""
from __future__ import annotations

import argparse

from app.db.session import create_schema
from app.services.book_map import backfill_missing_book_maps


def backfill(*, batch_size: int = 100, dry_run: bool = False) -> tuple[int, int, int]:
    create_schema()
    result = backfill_missing_book_maps(batch_size=batch_size, dry_run=dry_run)
    return result["created"], result["skipped"], result["failed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    made, skipped, failed = backfill(batch_size=args.batch_size, dry_run=args.dry_run)
    print(f"Book Maps created: {made}; skipped: {skipped}; failed: {failed}; dry_run={args.dry_run}")
