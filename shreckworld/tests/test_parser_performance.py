"""Adapter contracts without loading Docling models or downloading weights."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from shreckdoc.pipeline import DoclingAdapter, TokenCountingTokenizer, _token_count, build_chunks, normalize_document
from semantic_helpers import WordTokenizer


@pytest.fixture
def docling(monkeypatch):
    observed = {}

    class Options(BaseModel):
        model_config = {"extra": "allow", "arbitrary_types_allowed": True}
        layout_batch_size: int = 4
        table_batch_size: int = 4
        table_structure_options: object = Field(default_factory=lambda: SimpleNamespace(mode="fast"))
        heading_hierarchy_options: object = Field(default_factory=lambda: SimpleNamespace(enabled=False, use_bookmarks=False, use_style=False))

    class Document:
        def export_to_dict(self):
            return {"pages": {"1": {"size": {"width": 600, "height": 800}}}, "texts": [{"text": "Native evidence"}]}

        def export_to_markdown(self, **kwargs):
            observed["markdown"] = True
            return "Native evidence"

        def iterate_items(self, **kwargs):
            return iter([])

    class Converter:
        def __init__(self, **kwargs):
            observed["options"] = kwargs["format_options"]["pdf"].pipeline_options

        def convert(self, path):
            return SimpleNamespace(status=observed.get("status", "success"), document=Document(), errors=[], warnings=[])

    modules = {
        "docling.datamodel.base_models": {"InputFormat": SimpleNamespace(PDF="pdf")},
        "docling.datamodel.pipeline_options": {"PdfPipelineOptions": Options, "TableFormerMode": SimpleNamespace(ACCURATE="accurate")},
        "docling.datamodel.accelerator_options": {"AcceleratorOptions": lambda **kwargs: SimpleNamespace(**kwargs)},
        "docling.document_converter": {"DocumentConverter": Converter, "PdfFormatOption": lambda **kwargs: SimpleNamespace(**kwargs)},
        "docling_core.types.doc": {"ImageRefMode": SimpleNamespace(PLACEHOLDER="placeholder")},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    return observed


@pytest.mark.parametrize("markdown", [False, True])
def test_adapter_uses_memory_exports_and_preserves_quality_options(tmp_path, docling, markdown):
    adapter = DoclingAdapter(device="cpu", num_threads=8, layout_batch_size=8,
                             table_batch_size=2, export_markdown=markdown, require_complete=True)
    _, canonical, text, _, _ = adapter.parse(tmp_path / "source.pdf", tmp_path)
    assert json.loads((tmp_path / "docling_document.json").read_text()) == canonical
    assert (tmp_path / "document.md").exists() is markdown
    assert bool(text) is markdown
    assert docling.get("markdown", False) is markdown
    options = docling["options"]
    assert options.layout_batch_size == 8 and options.table_batch_size == 2
    assert options.accelerator_options.num_threads == 8
    assert options.table_structure_options.mode == "accurate"
    assert options.heading_hierarchy_options.enabled
    assert not options.do_ocr and not options.generate_page_images and not options.generate_picture_images
    assert set(adapter.phase_seconds) >= {"conversion", "canonical_export", "primitive_extraction"}


def test_incomplete_conversion_does_not_export(tmp_path, docling):
    docling["status"] = "partial_success"
    with pytest.raises(RuntimeError, match="partial_success"):
        DoclingAdapter(require_complete=True).parse(tmp_path / "source.pdf", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unsupported_tuning_fails_explicitly(tmp_path, docling, monkeypatch):
    options = sys.modules["docling.datamodel.pipeline_options"].PdfPipelineOptions
    monkeypatch.delitem(options.model_fields, "table_batch_size")
    with pytest.raises(RuntimeError, match="does not support table_batch_size"):
        DoclingAdapter(table_batch_size=8).parse(tmp_path / "source.pdf", tmp_path)


def test_bounded_token_counts_preserve_chunks_and_reduce_tokenization(tmp_path):
    class CountingTokenizer(WordTokenizer):
        calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return super().__call__(*args, **kwargs)

    document = normalize_document(
        ingestion_id="job", pdf_path=tmp_path / "source.txt", canonical={"pages": {"1": {}}},
        items=[{"reference": "body", "block_type": "paragraph", "text": "A rule applies. " * 200,
                "provenance": [{"page_no": 1}]}], page_labeler=lambda *_: {1: "1"},
    )
    raw, cached_raw = CountingTokenizer(), CountingTokenizer()
    cached = TokenCountingTokenizer(cached_raw)
    args = dict(document=document, ingestion_id="job", library_item_id="item", ontology_id="world",
                book_title="Rules", rpg_system="Cthulhu", max_embedding_tokens=100)
    assert build_chunks(tokenizer=raw, **args) == build_chunks(tokenizer=cached, **args)
    assert cached_raw.calls < raw.calls
    before = cached_raw.calls
    assert _token_count(cached, "passage: same input") == _token_count(cached, "passage: same input")
    assert cached_raw.calls == before + 1
    small = TokenCountingTokenizer(cached_raw, maxsize=1)
    _token_count(small, "one")
    _token_count(small, "two")
    _token_count(small, "one")
    assert small.count_tokens.cache_info().currsize == 1
