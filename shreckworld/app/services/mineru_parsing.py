"""MinerU Tasks API adapter producing ShreckDoc-compatible structure records."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _api_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _same_origin(base_url: str, target: str) -> bool:
    base, candidate = urlsplit(base_url), urlsplit(urljoin(base_url.rstrip("/") + "/", target))
    return (base.scheme, base.hostname, base.port) == (candidate.scheme, candidate.hostname, candidate.port)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        preferred = ("content", "text", "title_content", "paragraph_content", "table_content",
                     "image_caption", "table_caption", "caption", "list_items", "math_content",
                     "code_content", "algorithm_content")
        parts = [_text(value[key]) for key in preferred if key in value]
        return " ".join(part for part in parts if part)
    return ""


def _normalize_result(payload: dict[str, Any], pdf_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError("MinerU structured result has no pages array")
    try:
        import pymupdf
        with pymupdf.open(pdf_path) as pdf:
            sizes = [(float(page.rect.width), float(page.rect.height)) for page in pdf]
    except Exception as exc:
        raise ValueError("Unable to read PDF page dimensions for MinerU output") from exc
    layout_rows = payload.get("extensions", {}).get("docvortex_layout", {}).get("pages", [])
    layout_by_index = {int(row["page_idx"]): row for row in layout_rows if isinstance(row, dict) and "page_idx" in row}
    canonical_pages: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    order = 0
    for fallback_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_index = int(page.get("page_idx", fallback_index))
        page_number = page_index + 1
        width, height = sizes[page_index] if 0 <= page_index < len(sizes) else (None, None)
        canonical_pages[str(page_number)] = {"size": {"width": width, "height": height}}
        for block_index, block in enumerate(page.get("blocks", [])):
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type", "text")).lower()
            content = block.get("content", "")
            text = _text(content).strip()
            if not text:
                continue
            detail = content if isinstance(content, dict) else {}
            level = int(block.get("text_level") or block.get("level") or detail.get("level") or 0)
            if kind in {"doc_title", "paragraph_title", "title", "heading"} or (kind == "text" and level > 0):
                block_type = "heading"
                level = max(1, level)
            elif kind in {"table", "chart", "image", "figure", "seal"}:
                block_type = "table" if kind == "table" else "picture"
                captions = block.get("caption") or detail.get("caption") or detail.get("image_caption") or detail.get("table_caption")
                caption_text = _text(captions).strip()
                if caption_text and caption_text not in text:
                    text = f"{text} {caption_text}".strip()
            elif kind in {"header", "page_header"}:
                block_type = "page_header"
            elif kind in {"footer", "page_footer", "page_number"}:
                block_type = "page_footer"
            elif kind in {"list", "index"}:
                block_type = "list"
            elif kind in {"equation", "equation_interline", "formula"}:
                block_type = "formula"
            elif kind in {"code", "algorithm"}:
                block_type = "code"
            else:
                block_type = "paragraph"
            provenance: dict[str, Any] = {"page_no": page_number}
            box = block.get("bbox")
            dimensions = layout_by_index.get(page_index, {})
            page_width = dimensions.get("width_pt") or width
            page_height = dimensions.get("height_pt") or height
            if isinstance(box, list) and len(box) == 4 and page_width and page_height:
                x0, y0, x1, y1 = (float(value) for value in box)
                # MinerU's content list uses normalized 0-1000 coordinates.
                if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1000:
                    x0, x1 = x0 * page_width / 1000, x1 * page_width / 1000
                    y0, y1 = y0 * page_height / 1000, y1 * page_height / 1000
                provenance["bbox"] = {"left": x0, "top": y0, "right": x1, "bottom": y1, "coord_origin": "TOPLEFT"}
            item: dict[str, Any] = {"reference": f"mineru-{page_number}-{block.get('index', block_index)}",
                                    "block_type": block_type, "text": text, "reading_order": order,
                                    "provenance": [provenance]}
            if block_type == "heading":
                item["level"] = level
            items.append(item)
            order += 1
    if len(canonical_pages) != len(sizes):
        raise ValueError("MinerU did not return structure for every PDF page")
    return {"pages": canonical_pages}, items


def parse_pdf(path: Path, *, output_dir: Path, heartbeat: Callable[[], None],
              transport: httpx.BaseTransport | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Upload one PDF to the local MinerU Tasks API and normalize its result."""
    settings = get_settings()
    base_url = settings.mineru_api_url.rstrip("/")
    headers = _api_headers(settings.mineru_api_key)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    with httpx.Client(timeout=httpx.Timeout(60, read=120), follow_redirects=False, transport=transport) as client:
        response = client.post(f"{base_url}/v1/tasks", headers=headers, json={
            "files": [{"file_name": path.name, "bytes": path.stat().st_size, "sha256sum": digest.hexdigest()}],
            "tier": settings.mineru_tier, "output_formats": ["default"],
        })
        response.raise_for_status()
        receipt = response.json()
        task_id = receipt.get("task_id")
        receipt_files = receipt.get("files", [])
        if not task_id or not receipt_files:
            raise ValueError("MinerU returned an invalid task receipt")
        file_id = receipt_files[0].get("file_id")
        if not file_id:
            raise ValueError("MinerU returned no file ID")
        upload = receipt_files[0].get("upload")
        if upload:
            upload_url = upload.get("url")
            if not upload_url or not _same_origin(base_url, upload_url):
                raise ValueError("MinerU returned an upload URL outside its configured service")
            with path.open("rb") as source:
                uploaded = client.put(upload_url, headers=upload.get("headers", {}), content=source,
                                      timeout=httpx.Timeout(60, read=settings.mineru_timeout_seconds))
            uploaded.raise_for_status()
        deadline = time.monotonic() + settings.mineru_timeout_seconds
        while time.monotonic() < deadline:
            heartbeat()
            task_url = f"{base_url}/v1/tasks/{quote(str(task_id), safe='')}"
            status_response = client.get(task_url, headers=headers)
            status_response.raise_for_status()
            task = status_response.json()
            file_result = next((entry for entry in task.get("files", []) if entry.get("file_id") == file_id), None)
            if file_result is None:
                raise ValueError("MinerU task response omitted the submitted file")
            state = file_result.get("state") or file_result.get("status")
            if state == "completed":
                result_url = file_result.get("result_url")
                if not result_url or not _same_origin(base_url, result_url):
                    raise ValueError("MinerU returned a result URL outside its configured service")
                output_dir.mkdir(parents=True, exist_ok=True)
                archive_path = output_dir / "mineru_result.zip"
                try:
                    with client.stream("GET", result_url, headers=headers,
                                       timeout=httpx.Timeout(60, read=settings.mineru_timeout_seconds)) as archive_response:
                        archive_response.raise_for_status()
                        with archive_path.open("wb") as destination:
                            for chunk in archive_response.iter_bytes(1024 * 1024):
                                destination.write(chunk)
                    with zipfile.ZipFile(archive_path) as archive:
                        members = [name for name in archive.namelist()
                                   if PurePosixPath(name).name == "structured_content.json"]
                        if len(members) != 1:
                            raise ValueError("MinerU result archive must contain exactly one structured JSON file")
                        payload = json.loads(archive.read(members[0]))
                finally:
                    archive_path.unlink(missing_ok=True)
                canonical, items = _normalize_result(payload, path)
                version = payload.get("metadata", {}).get("producer", {}).get("version") or file_result.get("parser_version") or "unknown"
                return canonical, items, str(version)
            if state in {"failed", "canceled", "partial"}:
                raise RuntimeError(f"MinerU parsing ended with state {state}")
            time.sleep(settings.mineru_poll_interval_seconds)
        raise TimeoutError("MinerU parsing exceeded the configured deadline")


def check_ready() -> dict[str, Any]:
    settings = get_settings()
    headers = _api_headers(settings.mineru_api_key)
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        response = client.get(f"{settings.mineru_api_url.rstrip('/')}/v1/tiers", headers=headers)
        response.raise_for_status()
        tiers = response.json()
    return {"available": True, "configured_tier": settings.mineru_tier, "tiers": tiers}
