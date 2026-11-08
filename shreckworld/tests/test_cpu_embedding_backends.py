"""Backend boundaries use doubles; actual model benchmarks are deployment checks."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.embedding import (
    EmbeddingUnavailable, ShreckWorldEmbedder, backend_fingerprint,
    backend_model_kwargs, prepare_embedding_backend, snapshot_fingerprint,
)


def artifacts(path, backend):
    folder = path / backend
    folder.mkdir(parents=True)
    names = ["model.onnx", "model.onnx_data"] if backend == "onnx" else ["openvino_model.xml", "openvino_model.bin"]
    for name in names:
        (folder / name).write_bytes(name.encode())
    (path / "modules.json").write_text("[]")


def test_default_and_shared_frontend_config_precedence(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.delenv("SHRECKWORLD_EMBEDDING_BACKEND", raising=False)
    path = tmp_path / "shreckworld.json"
    monkeypatch.setenv("SHRECKWORLD_RUNTIME_CONFIG_FILE", str(path))
    assert get_settings().embedding_backend == "onnx"
    path.write_text('{"embedding_backend": "openvino"}')
    assert get_settings().embedding_backend == "openvino"
    monkeypatch.setenv("SHRECKWORLD_EMBEDDING_BACKEND", "")
    assert get_settings().embedding_backend == "openvino"
    monkeypatch.setenv("SHRECKWORLD_EMBEDDING_BACKEND", "torch")
    assert get_settings().embedding_backend == "torch"
    monkeypatch.setenv("SHRECKWORLD_EMBEDDING_DEVICE", "cuda")
    assert get_settings().embedding_device == "cuda"
    monkeypatch.delenv("SHRECKWORLD_EMBEDDING_BACKEND")
    with pytest.raises(ValidationError, match="require EMBEDDING_DEVICE=cpu"):
        get_settings()


def test_malformed_frontend_config_is_not_silently_ignored(tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.delenv("SHRECKWORLD_EMBEDDING_BACKEND", raising=False)
    path = tmp_path / "shreckworld.json"
    path.write_text("broken")
    monkeypatch.setenv("SHRECKWORLD_RUNTIME_CONFIG_FILE", str(path))
    with pytest.raises(ValidationError):
        get_settings()


@pytest.mark.parametrize("backend", ["onnx", "openvino"])
def test_cpu_only_settings_and_invalid_backend(backend):
    assert Settings(embedding_backend=backend).embedding_device == "cpu"
    with pytest.raises(ValidationError, match="require EMBEDDING_DEVICE=cpu"):
        Settings(embedding_backend=backend, embedding_device="cuda")
    with pytest.raises(ValidationError):
        Settings(embedding_backend="unknown")


def test_runtime_options_use_local_cpu_optimization(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=SimpleNamespace, GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=99)))
    options = backend_model_kwargs(Settings(embedding_backend="onnx", embedding_threads=3), export=False)
    assert options["provider"] == "CPUExecutionProvider" and options["export"] is False
    assert options["local_files_only"] is True
    assert options["session_options"].intra_op_num_threads == 3
    assert options["session_options"].graph_optimization_level == 99
    options = backend_model_kwargs(Settings(embedding_backend="openvino", embedding_threads=2), export=True)
    assert options["ov_config"]["INFERENCE_NUM_THREADS"] == 2
    assert options["ov_config"]["INFERENCE_PRECISION_HINT"] == "f32"
    assert options["load_in_8bit"] is False


@pytest.mark.parametrize("backend", ["onnx", "openvino"])
def test_export_once_runtime_never_exports_and_fingerprint_tracks_artifacts(tmp_path, monkeypatch, backend):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.safetensors").write_bytes(b"source weights")
    settings = Settings(embedding_backend=backend, embedding_model_id=str(source),
                        embedding_artifacts_path=tmp_path / "exports")
    constructor = Mock()
    constructor.return_value.save_pretrained.side_effect = lambda path: artifacts(Path(path), backend)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=Mock()))
    monkeypatch.setattr("app.services.embedding.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.embedding.backend_model_kwargs", lambda settings, export: {"export": export})
    runtime = ShreckWorldEmbedder()
    with pytest.raises(EmbeddingUnavailable, match="prefetch"):
        runtime._load_model()
    assert constructor.call_count == 0 and runtime._model is None
    prepare_embedding_backend(settings)
    assert constructor.call_args.kwargs["model_kwargs"]["export"] is True
    prepare_embedding_backend(settings)
    assert constructor.call_count == 1
    runtime._load_model()
    assert constructor.call_args.kwargs["model_kwargs"]["export"] is False
    assert constructor.call_args.kwargs["local_files_only"] is True
    assert runtime.backend == backend and runtime.revision == "source"
    original = runtime.fingerprint
    base = snapshot_fingerprint(source)
    assert original != base
    exported = settings.embedding_artifacts_path / base / backend
    assert backend_fingerprint(exported, backend, base) == original
    (exported / "modules.json").write_text('["changed pooling"]')
    assert backend_fingerprint(exported, backend, base) != original
    weights = exported / backend / ("model.onnx_data" if backend == "onnx" else "openvino_model.bin")
    before = backend_fingerprint(exported, backend, base)
    weights.write_bytes(b"changed weights")
    assert backend_fingerprint(exported, backend, base) != before


def test_failed_export_is_not_published(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.safetensors").write_bytes(b"source weights")
    settings = Settings(embedding_backend="onnx", embedding_model_id=str(source),
                        embedding_artifacts_path=tmp_path / "exports")
    constructor = Mock(side_effect=RuntimeError("export failed"))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor))
    monkeypatch.setattr("app.services.embedding.backend_model_kwargs", lambda settings, export: {})
    with pytest.raises(RuntimeError, match="export failed"):
        prepare_embedding_backend(settings)
    assert not (settings.embedding_artifacts_path / snapshot_fingerprint(source) / "onnx").exists()
