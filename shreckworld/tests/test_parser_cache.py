import json
import logging
from dataclasses import asdict

import pytest

from app.services.document_parsing import parse_source
from app.services.parser_cache import ParserPayload, cache_identity, read_cache, write_cache
from app.core.config import Settings
from test_document_metadata import pdf_bytes


def test_parser_and_embedding_devices_can_be_configured_independently():
    settings = Settings(parser_device="cuda", embedding_device="cpu", embedding_backend="onnx")
    assert settings.parser_device == "cuda"
    assert settings.embedding_device == "cpu"
    assert settings.embedding_backend == "onnx"


def test_shared_runtime_config_supplies_settings_but_environment_can_override(tmp_path, monkeypatch):
    from shreckconfig.shreckworld import ShreckWorldRuntimeConfig
    from app.core import config

    path = tmp_path / "shreckworld.json"
    path.write_text(ShreckWorldRuntimeConfig(
        embedding_backend="onnx", embedding_device="cpu", pdf_parser="docling",
        parser_device="cuda", parser_table_structure_enabled=True, parser_table_former_mode="accurate",
    ).model_dump_json())
    monkeypatch.setattr(config, "runtime_config_file", lambda: path)
    for key in (
        "SHRECKWORLD_EMBEDDING_BACKEND", "SHRECKWORLD_EMBEDDING_DEVICE", "SHRECKWORLD_PDF_PARSER",
        "SHRECKWORLD_PARSER_DEVICE", "SHRECKWORLD_PARSER_TABLE_STRUCTURE_ENABLED",
        "SHRECKWORLD_PARSER_TABLE_FORMER_MODE",
    ):
        monkeypatch.setenv(key, "")
    settings = config.get_settings()
    assert settings.parser_device == "cuda" and settings.parser_table_former_mode == "accurate"
    assert settings.embedding_backend == "onnx" and settings.embedding_device == "cpu"

    monkeypatch.setenv("SHRECKWORLD_PARSER_DEVICE", "cpu")
    assert config.get_settings().parser_device == "cpu"


@pytest.mark.parametrize(("cuda_available", "expected"), [(True, "cuda"), (False, "cpu")])
def test_auto_parser_device_uses_cuda_when_available_and_cpu_otherwise(tmp_path, monkeypatch, cuda_available, expected):
    import torch
    from shreckconfig.shreckworld import ShreckWorldRuntimeConfig
    from app.core import config

    path = tmp_path / "shreckworld.json"
    path.write_text(ShreckWorldRuntimeConfig(parser_device="auto").model_dump_json())
    monkeypatch.setattr(config, "runtime_config_file", lambda: path)
    monkeypatch.setenv("SHRECKWORLD_PARSER_DEVICE", "")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)

    assert config.get_settings().parser_device == expected


@pytest.fixture
def parser(tmp_path, monkeypatch):
    from shreckconfig.shreckworld import ShreckWorldRuntimeConfig
    runtime_config = tmp_path / "shreckworld-runtime.json"
    runtime_config.write_text(ShreckWorldRuntimeConfig(parser_device="cpu").model_dump_json())
    monkeypatch.setattr("app.core.config.runtime_config_file", lambda: runtime_config)
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes())
    monkeypatch.setenv("SHRECKWORLD_DOCLING_ARTIFACTS_PATH", str(tmp_path / "models"))
    monkeypatch.setenv("SHRECKWORLD_PARSER_DEVICE", "cpu")
    monkeypatch.setattr("app.services.document_parsing.version", lambda _: "test-parser")
    calls = []

    class Adapter:
        def __init__(self, *args, **kwargs):
            assert kwargs["require_complete"] and not kwargs["export_markdown"]
            assert kwargs["num_threads"] == 4
            assert kwargs["table_former_mode"] == "fast"
            assert kwargs["device"] == Settings().parser_device
            assert kwargs["table_structure_enabled"] is Settings().parser_table_structure_enabled
            self.phase_seconds = {"conversion": 0.25}
            self.progress = kwargs["on_page_progress"]

        def parse(self, path, output):
            calls.append(path)
            self.progress(1, 1)
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


def test_fast_table_mode_does_not_reuse_accurate_mode_cache(parser, monkeypatch):
    import app.services.parser_cache as parser_cache

    source, _, _ = parser
    settings = Settings()
    assert settings.parser_table_structure_enabled is True
    fast_key = cache_identity(source, settings, "test-parser")
    original_dumps = json.dumps

    def accurate_identity(value, **kwargs):
        if isinstance(value, dict) and value.get("tables") == "fast":
            value = {**value, "tables": "accurate"}
        return original_dumps(value, **kwargs)

    monkeypatch.setattr(parser_cache.json, "dumps", accurate_identity)
    assert cache_identity(source, settings, "test-parser") != fast_key


def test_parser_adapter_versions_invalidate_only_the_selected_adapter_cache(parser, monkeypatch):
    import app.services.parser_cache as parser_cache

    source, _, _ = parser
    settings = Settings()
    docling_key = cache_identity(source, settings, "test-parser")
    pymupdf_key = cache_identity(source, settings, "test-parser", parser_name="pymupdf")
    monkeypatch.setattr(parser_cache, "DOCLING_ADAPTER_VERSION", 1)
    assert cache_identity(source, settings, "test-parser") != docling_key
    assert cache_identity(source, settings, "test-parser", parser_name="pymupdf") == pymupdf_key
    monkeypatch.setattr(parser_cache, "PYMUPDF_ADAPTER_VERSION", 1)
    assert cache_identity(source, settings, "test-parser", parser_name="pymupdf") != pymupdf_key
    mineru_key = cache_identity(source, settings, "test-parser", parser_name="mineru")
    monkeypatch.setattr(parser_cache, "MINERU_ADAPTER_VERSION", 1)
    assert cache_identity(source, settings, "test-parser", parser_name="mineru") != mineru_key


def test_page_callback_is_marshaled_back_to_calling_thread(parser, tmp_path):
    import threading
    source, _, _ = parser
    observed = []
    caller = threading.get_ident()
    parse_source(source, ingestion_id="progress", output_dir=tmp_path / "progress",
                 heartbeat=lambda _: None,
                 page_progress=lambda done, total: observed.append((done, total, threading.get_ident())))
    assert observed == [(1, 1, caller)]


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


@pytest.mark.parametrize("change", ["source", "parser", "threads", "layout_batch", "table_batch", "table_structure", "parser_device", "table_mode", "artifacts", "corrupt", "checksum", "disabled"])
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
    elif change == "parser_device":
        original = cache_identity(source, Settings(parser_device="cpu"), "test-parser")
        assert cache_identity(source, Settings(parser_device="cuda"), "test-parser") != original
        return
    elif change == "table_mode":
        original = cache_identity(source, Settings(parser_table_former_mode="fast"), "test-parser")
        assert cache_identity(source, Settings(parser_table_former_mode="accurate"), "test-parser") != original
        return
    elif change in {"layout_batch", "table_batch"}:
        monkeypatch.setenv(f"SHRECKWORLD_PARSER_{change.upper()}_SIZE", "8")
    elif change == "table_structure":
        monkeypatch.setenv("SHRECKWORLD_PARSER_TABLE_STRUCTURE_ENABLED", "false")
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


def test_mineru_cache_identity_tracks_service_version_tier_and_endpoint(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"same source")
    base = Settings(pdf_parser="mineru", mineru_api_url="http://mineru:8000", mineru_version="4.1.7", mineru_tier="standard")
    key = cache_identity(source, base, "remote", parser_name="mineru")
    assert cache_identity(source, base, "remote", parser_name="mineru") == key
    assert cache_identity(source, Settings(**{**base.model_dump(), "mineru_version": "4.2.0"}), "remote", parser_name="mineru") != key
    assert cache_identity(source, Settings(**{**base.model_dump(), "mineru_tier": "advanced"}), "remote", parser_name="mineru") != key
    assert cache_identity(source, Settings(**{**base.model_dump(), "mineru_api_url": "http://mineru-new:8000"}), "remote", parser_name="mineru") != key
