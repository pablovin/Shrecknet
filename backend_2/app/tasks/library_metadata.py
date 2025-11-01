"""Celery task to extract PDF metadata and cover image for LibraryItem."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from app.celery_app import celery_app
from app.core.config import get_settings
from app.repositories.library_repository import LibraryRepository
from app.utils.async_helpers import run_async


@celery_app.task(name="library.extract_metadata")
def extract_metadata(library_item_id: int) -> dict[str, Any]:
    """Extract title, authors, description and cover image from a PDF.

    Updates the LibraryItem row in the relational DB. Writes cover under
    media/library/{ontology_id}/{item_id}/cover.png via MediaService.
    """
    settings = get_settings()

    async def _impl() -> dict[str, Any]:
        from app.db.session import AsyncSessionMaker
        import fitz  # PyMuPDF
        from fastapi import UploadFile
        from app.services.media_service import MediaService

        async with AsyncSessionMaker() as session:
            repo = LibraryRepository(session)
            item = await repo.get_item_by_id(library_item_id)
            if not item:
                return {"status": "not_found"}

            # Resolve PDF path
            if not item.pdf_path:
                return {"status": "no_pdf"}
            pdf_path = Path(settings.media_root) / item.pdf_path
            if not pdf_path.exists():
                return {"status": "missing_pdf"}

            # Extract metadata
        try:
            doc = fitz.open(str(pdf_path))
            meta = doc.metadata or {}
            title = (meta.get("title") or "").strip() or None
            # Some generators use 'name' instead of 'title'
            if not title:
                maybe_name = (meta.get("name") or "").strip()
                title = maybe_name or None
            authors = (meta.get("author") or "").strip() or None
            description = (meta.get("subject") or "").strip() or None
        finally:
            try:
                doc.close()
            except Exception:
                pass

        # Fallback: try pypdf metadata and XMP if missing
        if not title or not authors:
            try:
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(str(pdf_path))
                info = getattr(reader, "metadata", None) or getattr(reader, "documentInfo", None)
                if (not title) and info:
                    t = None
                    for key in ("/Title", "Title", "/Name", "Name"):
                        t = getattr(info, key, None) if hasattr(info, key) else (info.get(key) if isinstance(info, dict) else None)
                        if t:
                            break
                    if isinstance(t, str):
                        t = t.strip()
                    title = t or title
                if (not authors) and info:
                    a = None
                    for key in ("/Author", "Author"):
                        a = getattr(info, key, None) if hasattr(info, key) else (info.get(key) if isinstance(info, dict) else None)
                        if a:
                            break
                    if isinstance(a, str):
                        a = a.strip()
                    authors = a or authors
                # XMP metadata (dc:title, dc:creator)
                xmp = getattr(reader, "xmp_metadata", None)
                if xmp:
                    if not title:
                        try:
                            xt = getattr(xmp, "dc_title", None)
                            if isinstance(xt, dict):
                                # pick any language
                                xt_val = next((v for v in xt.values() if v), None)
                                if xt_val:
                                    title = str(xt_val).strip()
                        except Exception:
                            pass
                    if not authors:
                        try:
                            creators = getattr(xmp, "dc_creator", None)
                            if creators:
                                if isinstance(creators, list):
                                    authors = ", ".join([str(c) for c in creators if c]) or None
                                else:
                                    authors = str(creators)
                        except Exception:
                            pass
            except Exception:
                pass

        # Last-resort: filename as title
        if not title:
            stem = pdf_path.stem.replace("_", " ").strip()
            title = stem.title() if stem else None

            data: dict[str, Any] = {}
            if (not item.title) or item.title == "Untitled":
                if title:
                    data["title"] = title
            if not item.authors and authors:
                data["authors"] = authors
            if not item.description and description:
                data["description"] = description

            # Extract cover image if missing
            cover_url: str | None = None
            if not item.cover_url:
                try:
                    doc2 = fitz.open(str(pdf_path))
                    if len(doc2) > 0:
                        pix = doc2[0].get_pixmap(dpi=150)
                        img_bytes = BytesIO(pix.tobytes("png"))
                        img = Image.open(img_bytes)
                        out = BytesIO()
                        img.save(out, format="PNG")
                        out.seek(0)
                        upload = UploadFile(file=out, filename="cover.png", headers={"content-type": "image/png"})
                        media = MediaService()
                        cover_url = await media.save_content_image(
                            upload,
                            content_type="library",
                            content_id=f"{item.ontology_id}/{item.id}",
                            is_main=True,
                            resize=(800, 1200),
                        )
                        if cover_url:
                            data["cover_url"] = cover_url
                except Exception:
                    pass
                finally:
                    try:
                        doc2.close()
                    except Exception:
                        pass

            if data:
                await repo.update_item(item, data)
                await session.commit()

            return {
                "status": "ok",
                "updated": list(data.keys()),
                "cover_url": data.get("cover_url", item.cover_url),
            }

    return run_async(_impl())
