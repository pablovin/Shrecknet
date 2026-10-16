from types import SimpleNamespace

import pytest

from app.services.embedding import EmbeddingInputError, EmbeddingUnavailable, ShreckWorldEmbedder, snapshot_fingerprint
from semantic_helpers import WordTokenizer


class Model:
    max_seq_length = 100
    def __init__(self):
        self.tokenizer = WordTokenizer()
        self.inputs = []
    def get_sentence_embedding_dimension(self):
        return 2
    def encode(self, texts, **kwargs):
        self.inputs.extend(texts)
        return [[0.0, 1.0] if "Bread" in text else [1.0, 0.0] for text in texts]


@pytest.fixture
def encoder(monkeypatch):
    runtime = ShreckWorldEmbedder()
    runtime.settings = SimpleNamespace(embedding_model_id="test-e5", embedding_dimension=2, embedding_batch_size=2, embedding_device="cpu")
    runtime.fingerprint, runtime.revision = "a" * 64, "revision"
    model = Model()
    runtime._model = model
    monkeypatch.setattr(runtime, "_load_model", lambda: model)
    return runtime, model


def test_actual_inference_probe_and_e5_prefixes_once(encoder):
    runtime, model = encoder
    assert runtime.ensure_ready()["ready"]
    assert runtime.embed("passage: semantic evidence") == [1.0, 0.0]
    assert runtime.embed("query: a question", query=True) == [1.0, 0.0]
    assert model.inputs[-2:] == ["passage: semantic evidence", "query: a question"]
    assert runtime.token_limit == 100
    assert runtime.tokenizer is model.tokenizer


def test_missing_or_broken_encoder_never_hashes(encoder, monkeypatch):
    runtime, _ = encoder
    def unavailable():
        raise ImportError("missing semantic dependency")
    monkeypatch.setattr(runtime, "_load_model", unavailable)
    with pytest.raises(EmbeddingUnavailable, match="ImportError"):
        runtime.embed("text")
    assert not hasattr(runtime, "_hash_vector")


@pytest.mark.parametrize("vector", [[1.0], [float("nan"), 0], [float("inf"), 0], [0, 0], [2, 0]])
def test_invalid_vectors_fail_readiness(encoder, monkeypatch, vector):
    runtime, model = encoder
    monkeypatch.setattr(model, "encode", lambda texts, **kwargs: [vector for _ in texts])
    with pytest.raises(EmbeddingUnavailable):
        runtime.ensure_ready()


def test_wrong_dimension_and_constant_encoder_fail(encoder, monkeypatch):
    runtime, model = encoder
    monkeypatch.setattr(model, "get_sentence_embedding_dimension", lambda: 3)
    with pytest.raises(EmbeddingUnavailable, match="dimension"):
        runtime.ensure_ready()
    monkeypatch.setattr(model, "get_sentence_embedding_dimension", lambda: 2)
    monkeypatch.setattr(model, "encode", lambda texts, **kwargs: [[1, 0] for _ in texts])
    with pytest.raises(EmbeddingUnavailable, match="relevance self-test"):
        runtime.ensure_ready()


def test_oversized_input_is_rejected_before_inference(encoder):
    runtime, model = encoder
    runtime.ensure_ready()
    before = len(model.inputs)
    with pytest.raises(EmbeddingInputError, match="truncation is disabled"):
        runtime.embed("word " * 150)
    assert len(model.inputs) == before


def test_runtime_failure_and_count_mismatch_do_not_fall_back(encoder, monkeypatch):
    runtime, model = encoder
    runtime.ensure_ready()
    def fail(*args, **kwargs):
        raise RuntimeError("inference failed")
    monkeypatch.setattr(model, "encode", fail)
    with pytest.raises(EmbeddingUnavailable, match="inference failed"):
        runtime.embed("text")
    monkeypatch.setattr(model, "encode", lambda texts, **kwargs: [])
    with pytest.raises(EmbeddingUnavailable, match="number of vectors"):
        runtime.embed("text")


def test_fingerprint_tracks_actual_weights_and_tokenizer(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    (tmp_path / "tokenizer.json").write_text("{}")
    first = snapshot_fingerprint(tmp_path)
    (tmp_path / "tokenizer.json").write_text('{"changed": true}')
    assert snapshot_fingerprint(tmp_path) != first
    (tmp_path / "model.safetensors").unlink()
    with pytest.raises(EmbeddingUnavailable, match="no encoder weights"):
        snapshot_fingerprint(tmp_path)
