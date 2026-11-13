"""Controlled, embedding-free restoration of generation-pinned Book Map V4."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ShreckWorldDocument, ShreckWorldDocumentBookMap
from app.schemas.book_map import BookMapV4
from shreckdoc.structure_validation import require_valid_book_map


def backfill_book_maps_v4(session: Session, *, batch_size: int = 100,
                          dry_run: bool = True) -> dict[str, int]:
    """Restore only validated V4 artifacts; missing extraction artifacts are classified."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    restored = skipped = needs_reparse = failed = aligned = legacy_context = 0
    documents = list(session.scalars(select(ShreckWorldDocument).order_by(ShreckWorldDocument.created_at)))
    for offset in range(0, len(documents), batch_size):
        for document in documents[offset:offset + batch_size]:
            existing = session.get(ShreckWorldDocumentBookMap, document.id)
            if existing is not None and existing.map_version == 4:
                skipped += 1
                continue
            artifact_path = Path(document.manifest_path).parent / "book_map_v4.json"
            if not artifact_path.is_file():
                needs_reparse += 1
                continue
            try:
                payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                tree = BookMapV4.model_validate(payload)
                require_valid_book_map(tree)
                if tree.document_id != document.id or tree.source_sha256 != document.source_sha256:
                    raise ValueError("artifact_generation_mismatch")
                if dry_run:
                    session.rollback()
                elif existing is None:
                    session.add(ShreckWorldDocumentBookMap(document_id=document.id, map_version=4,
                                                           payload=tree.model_dump(mode="json")))
                    session.commit()
                else:
                    existing.map_version, existing.payload = 4, tree.model_dump(mode="json")
                    session.commit()
                restored += 1
                if tree.index_alignment == "aligned":
                    aligned += 1
                else:
                    legacy_context += 1
            except Exception:
                session.rollback()
                failed += 1
    return {"restored": restored, "aligned": aligned, "legacy_context": legacy_context,
            "skipped": skipped, "needs_reparse": needs_reparse,
            "failed": failed, "dry_run": int(dry_run)}
