import json
import logging
from dataclasses import asdict

import pytest

from app.services.document_parsing import parse_source
from app.services.parser_cache import ParserPayload, cache_identity, read_cache, write_cache
from app.core.config import Settings
from test_document_metadata import pdf_bytes


@pytest.fixture
def parser(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes())
    monkeypatch.setenv("SHRECKWORLD_DOCLING_ARTIFACTS_PATH", str(tmp_path / "models"))
    monkeypatch.setattr("app.services.document_parsing.version", lambda _: "test-parser")
    calls = []

    class Adapter:
        def __init__(self, *args, **kwargs):
            assert kwargs["require_complete"] and not kwargs["export_markdown"]
            assert kwargs["num_threads"] == 4
            self.phase_seconds = {"conversion": 0.25}

        def parse(self, path, output):
            calls.append(path)
            logging.getLogger("docling.parser").warning("retained diagnostic")
            return None, {"pages": {"1": {"size": {"width": 600, "height": 800}}}}, "", [
                {"reference": "heading", "block_type": "heading", "text": "Combat", "level": 1,
                 "reading_order": 0, "provenance": [{"page_no": 1}]},
                {"reference": "body", "block_type": "paragraph", "text": "A combat rule.",
                 "reading_order": 1, "provenance": [{"page_no": 1, "bbox": {"left": 10, "top": 30}, "charspan": [0, 14]}]},
            ], []

    monkeypatch.setattr("app.services.document_parsing.DoclingAdapter", Adapter)

    def run(job):
        return parse_source(source, ingestion_id=job, output_dir=tmp_path / "parsed" / job, heartbeat=lambda _: None)

    return source, calls, run


def test_reindex_reuses_parse_with_fresh_ids_and_identical_evidence(parser, tmp_path):
    _, calls, run = parser
    first, second = run("first"), run("second")
    assert len(calls) == 1
    assert not first.cache_hit and second.cache_hit
    assert first.phase_seconds["conversion"] == 0.25
    assert "conversion" not in second.phase_seconds
    assert second.quality == first.quality
    assert first.diagnostics == second.diagnostics == ["warning: retained diagnostic"]
    assert second.document.pages[0].page_id != first.document.pages[0].page_id
    assert second.document.sections[1].section_id != first.document.sections[1].section_id
    old, new = asdict(first.document.blocks[1]), asdict(second.document.blocks[1])
    assert old.pop("block_id") != new.pop("block_id")
    assert old.pop("section_id") != new.pop("section_id")
    assert old == new
    assert (tmp_path / "parsed" / "second" / "docling_document.json").exists()
    assert (tmp_path / "parsed" / "second" / "normalized_document.json").exists()


@pytest.mark.parametrize("change", ["source", "parser", "threads", "layout_batch", "table_batch", "artifacts", "corrupt", "checksum", "disabled"])
def test_cache_invalidates_or_reparses_safely(parser, tmp_path, monkeypatch, change):
    source, calls, run = parser
    run("first")
    if change == "source":
        source.write_bytes(source.read_bytes() + b"\n% changed\n")
    elif change == "parser":
        monkeypatch.setattr("app.services.document_parsing.version", lambda _: "new-parser")
    elif change == "threads":
        # Use identity directly: the parser double deliberately expects 4 threads.
        original = cache_identity(source, Settings(), "test-parser")
        assert cache_identity(source, Settings(parser_threads=8), "test-parser") != original
        return
    elif change in {"layout_batch", "table_batch"}:
        monkeypatch.setenv(f"SHRECKWORLD_PARSER_{change.upper()}_SIZE", "8")
    elif change == "artifacts":
        root = tmp_path / "models"
        root.mkdir()
        (root / "model.bin").write_bytes(b"new weights")
    elif change in {"corrupt", "checksum"}:
        entry = next((tmp_path / "parser-cache").glob("*.json"))
        if change == "corrupt":
            entry.write_text("incomplete")
        else:
            data = json.loads(entry.read_text())
            data["payload"]["items"][1]["text"] = "corrupt text"
            entry.write_text(json.dumps(data))
    else:
        monkeypatch.setenv("SHRECKWORLD_PARSER_CACHE_ENABLED", "false")
    result = run("second")
    assert len(calls) == 2 and not result.cache_hit
    assert result.document.blocks[1].display_text == "A combat rule."


def test_parser_failure_never_publishes_cache(parser, tmp_path, monkeypatch):
    _, _, run = parser
    def fail(*args):
        raise RuntimeError("incomplete conversion")
    monkeypatch.setattr("app.services.document_parsing.DoclingAdapter.parse", fail)
    with pytest.raises(RuntimeError, match="incomplete"):
        run("failed")
    assert not list((tmp_path / "parser-cache").glob("*.json"))


def test_cache_publication_failure_is_optional(tmp_path, monkeypatch):
    payload = ParserPayload(canonical={"pages": {}}, items=[], diagnostics=[], quality={})
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    write_cache(blocked / "entry.json", "key", payload)
    assert read_cache(blocked / "entry.json", "key") is None


def test_concurrent_publications_are_complete(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "entry.json"
    payload = ParserPayload(canonical={"pages": {"1": {}}}, items=[], diagnostics=[], quality={})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: write_cache(path, "key", payload), range(8)))
    assert read_cache(path, "key") == payload
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("setting", ["parser_threads", "parser_layout_batch_size", "parser_table_batch_size", "embedding_batch_size"])
def test_tuning_settings_reject_invalid_values(setting):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(**{setting: 0})
