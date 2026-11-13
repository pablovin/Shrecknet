import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shreckconfig.shreckworld import ShreckWorldRuntimeConfig, publish_runtime_config, read_runtime_config


def test_runtime_projection_contains_parser_and_embedding_settings_and_replaces_atomically(tmp_path):
    path = tmp_path / "service-config/shreckworld.json"
    assert read_runtime_config(path) is None
    publish_runtime_config(path, ShreckWorldRuntimeConfig())
    assert json.loads(path.read_text()) == {
        "embedding_backend": "onnx", "embedding_device": "cpu", "pdf_parser": "docling",
        "parser_device": "auto", "parser_table_structure_enabled": True,
        "parser_table_former_mode": "fast",
    }
    before = path.stat().st_mtime_ns
    publish_runtime_config(path, ShreckWorldRuntimeConfig())
    assert path.stat().st_mtime_ns == before
    publish_runtime_config(path, ShreckWorldRuntimeConfig(embedding_backend="openvino", pdf_parser="pymupdf"))
    assert read_runtime_config(path).embedding_backend == "openvino"
    assert read_runtime_config(path).pdf_parser == "pymupdf"
    publish_runtime_config(path, ShreckWorldRuntimeConfig(pdf_parser="mineru"))
    assert read_runtime_config(path).pdf_parser == "mineru"
    assert len(list(path.parent.iterdir())) == 1


def test_legacy_backend_only_projection_keeps_compatible_defaults(tmp_path):
    path = tmp_path / "shreckworld.json"
    path.write_text('{"embedding_backend":"onnx"}')
    config = read_runtime_config(path)
    assert config is not None
    assert config.embedding_backend == "onnx"
    assert config.pdf_parser == "docling"
    assert config.parser_device == "auto"
    assert config.parser_table_former_mode == "fast"


def test_invalid_runtime_file_fails_explicitly(tmp_path):
    path = tmp_path / "shreckworld.json"
    for content in ['{"embedding_backend": "hash"}', '{"embedding_backend": "onnx", "secret": "value"}', 'broken']:
        path.write_text(content)
        with pytest.raises(ValidationError):
            read_runtime_config(path)


def test_onnx_and_openvino_reject_cuda_embedding_device():
    for backend in ("onnx", "openvino"):
        with pytest.raises(ValidationError, match="require embedding_device=cpu"):
            ShreckWorldRuntimeConfig(embedding_backend=backend, embedding_device="cuda")


def test_failed_publication_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "shreckworld.json"
    publish_runtime_config(path, ShreckWorldRuntimeConfig())
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        publish_runtime_config(path, ShreckWorldRuntimeConfig(embedding_backend="torch"))
    assert read_runtime_config(path).embedding_backend == "onnx"
    assert len(list(tmp_path.iterdir())) == 1
