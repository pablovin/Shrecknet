"""Run the artifact-only Book Map V4 backfill (dry-run unless --write)."""
from __future__ import annotations

import argparse

from app.db.session import session_factory
from app.services.book_map_v4_backfill import backfill_book_maps_v4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--write", action="store_true", help="persist validated map artifacts")
    args = parser.parse_args()
    with session_factory()() as session:
        print(backfill_book_maps_v4(session, batch_size=args.batch_size, dry_run=not args.write))


if __name__ == "__main__":
    main()
