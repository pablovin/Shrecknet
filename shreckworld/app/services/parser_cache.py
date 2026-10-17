"""Private, source-local cache of generation-independent successful parser output."""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import BaseModel, Field
from shreckdoc.pipeline import _sha256

logger = logging.getLogger(__name__)
CACHE_VERSION = 1


class ParserPayload(BaseModel):
    canonical: dict[str, Any]
    items: list[dict[str, Any]]
    diagnostics: list[str]
    quality: dict[str, int | None]
    markdown: str = ""


class CacheEntry(BaseModel):
    key: str
    checksum: str
    payload: ParserPayload
    format_version: int = Field(default=CACHE_VERSION)


@lru_cache(maxsize=8)
def _artifact_digest(files: tuple[tuple[str, str, int, int, int], ...]) -> str:
    digest = hashlib.sha256()
    for relative, absolute, *_ in files:
        digest.update(relative.encode())
        digest.update(bytes.fromhex(_sha256(Path(absolute))))
    return digest.hexdigest()


def cache_identity(path: Path, settings: Any, parser_version: str) -> str:
    artifacts = settings.docling_artifacts_path.resolve()
    files = []
    for file in sorted(artifacts.rglob("*")):
        if file.is_file():
            stat = file.stat()
            files.append((str(file.relative_to(artifacts)), str(file), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    dependencies = {}
    for name in ("shreckdoc", "docling-core", "docling-ibm-models", "docling-parse", "torch", "transformers"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    identity = {
        "format_version": CACHE_VERSION, "adapter_version": 1,
        "source_sha256": _sha256(path), "suffix": path.suffix.lower(),
        "parser_version": parser_version, "dependencies": dependencies,
        "artifacts": _artifact_digest(tuple(files)),
        "device": settings.embedding_device, "threads": settings.parser_threads,
        "layout_batch_size": settings.parser_layout_batch_size,
        "table_batch_size": settings.parser_table_batch_size,
        "markdown": settings.parser_export_markdown,
        "ocr": False, "tables": "accurate", "heading_inference": True,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _checksum(payload: ParserPayload) -> str:
    return hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_cache(path: Path, key: str) -> ParserPayload | None:
    try:
        entry = CacheEntry.model_validate_json(path.read_bytes())
        if entry.format_version != CACHE_VERSION or entry.key != key or entry.checksum != _checksum(entry.payload):
            raise ValueError("Parser cache identity/checksum mismatch")
        return entry.payload
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable or invalid parser cache %s", path)
        return None


def write_cache(path: Path, key: str, payload: ParserPayload) -> None:
    """Unique temporary files make concurrent complete publications safe."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = CacheEntry(key=key, checksum=_checksum(payload), payload=payload)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(entry.model_dump_json())
        temporary.replace(path)
    except OSError:
        logger.warning("Unable to publish optional parser cache %s", path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove parser cache temporary file %s", temporary)
