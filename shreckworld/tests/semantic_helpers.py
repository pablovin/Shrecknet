"""Small explicit model/parser doubles; unit tests never download model artifacts."""
from pathlib import Path
from types import SimpleNamespace

from shreckdoc.pipeline import normalize_document

from app.services.document_parsing import ParsedSource

FINGERPRINT = "a" * 64


class WordTokenizer:
    model_max_length = 512
    def __init__(self):
        self.words = []
    def encode(self, text, add_special_tokens=True):
        ids = []
        for word in text.split():
            if word not in self.words:
                self.words.append(word)
            ids.append(self.words.index(word) + 2)
        return ([0] if add_special_tokens else []) + ids + ([1] if add_special_tokens else [])
    def __call__(self, text, **kwargs):
        return {"input_ids": self.encode(text, kwargs.get("add_special_tokens", True))}
    def decode(self, ids, **kwargs):
        return " ".join(self.words[index - 2] for index in ids if index >= 2)


class SemanticRuntime:
    settings = SimpleNamespace(embedding_dimension=2, embedding_model_id="test-e5", embedding_batch_size=1)
    fingerprint = FINGERPRINT
    revision = "test-revision"
    token_limit = 100
    backend = "sentence_transformers"
    def __init__(self):
        self.tokenizer = WordTokenizer()
        self.before_batch = None
        self.inputs = []
    def ensure_ready(self):
        return {"ready": True, "fingerprint": self.fingerprint, "revision": self.revision}
    def embed_batch(self, texts, **kwargs):
        if self.before_batch:
            self.before_batch()
        self.inputs.extend(texts)
        assert all(text.startswith("passage: ") for text in texts)
        assert all(len(self.tokenizer.encode(text)) <= self.token_limit for text in texts)
        return [[1.0, 0.0] for text in texts]
    def embed(self, text, query=False):
        return [1.0, 0.0]


def parsed_source(path: Path, *, ingestion_id, output_dir, heartbeat):
    heartbeat(2)
    content = path.read_text()
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical = {"pages": {"1": {"size": {"width": 600, "height": 800}}, "2": {"size": {"width": 600, "height": 800}}}}
    items = [
        {"reference": "heading", "block_type": "heading", "text": "Combat", "level": 1, "reading_order": 0, "provenance": [{"page_no": 1}]},
        {"reference": "body", "block_type": "paragraph", "text": content, "reading_order": 1,
         "provenance": [{"page_no": 2, "bbox": {"left": 10, "top": 700, "right": 500, "bottom": 600, "coord_origin": "BOTTOMLEFT"}, "charspan": [0, len(content)]}]},
    ]
    normalized = normalize_document(ingestion_id=ingestion_id, pdf_path=path, canonical=canonical, items=items,
                                    page_labeler=lambda *_: {1: "i", 2: "1"})
    return ParsedSource(normalized, "docling", "test-parser", [], {"native_pdf_pages": 2, "docling_pages": 2, "tables_detected": 0})
