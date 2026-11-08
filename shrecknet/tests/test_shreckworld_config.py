import json

import pytest
from fastapi import HTTPException

from app.api.routers import configurations
from app.core import config_store as config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    monkeypatch.setenv("SHRECKNET_CONFIG_SEED_FILE", str(seed))
    monkeypatch.setattr(config, "_data_dir_cache", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(configurations, "get_settings", config.get_settings)
    monkeypatch.setattr(configurations, "update_settings", config.update_settings)
    monkeypatch.setattr(configurations, "configure_celery_app", lambda: None)
    return tmp_path / "service-config/shreckworld.json"


def test_schema_exposes_editable_restart_required_shreckworld_tab():
    schema = configurations.get_config_schema()
    group = next(group for group in schema["groups"] if group["id"] == "shreckworld")
    assert group == {"id": "shreckworld", "label": "Shreckworld",
                     "fields": ["shreckworld_embedding_backend"], "property": "restart_required"}
    meta = schema["field_meta"]["shreckworld_embedding_backend"]
    assert meta["options"] == ["onnx", "torch", "openvino"]
    assert meta["frontend_editable"] is True and meta["change_impact"] == "service_restart"
    assert "shreckworld_embedding_backend" not in schema["ungrouped_fields"]


@pytest.mark.asyncio
async def test_config_save_persists_and_publishes_backend(isolated_config):
    assert config.get_settings().shreckworld_embedding_backend == "onnx"
    assert json.loads(isolated_config.read_text()) == {"embedding_backend": "onnx"}
    for backend in ("torch", "openvino", "onnx"):
        response = await configurations.put_config({"shreckworld_embedding_backend": backend})
        assert response["shreckworld_embedding_backend"] == backend
        assert json.loads(isolated_config.read_text()) == {"embedding_backend": backend}
        assert config.reload_settings().shreckworld_embedding_backend == backend
    # Reload repairs a missing projection from the authoritative database.
    isolated_config.unlink()
    config.reload_settings()
    assert json.loads(isolated_config.read_text()) == {"embedding_backend": "onnx"}


@pytest.mark.parametrize("value", ["hash", "cuda", "", None, 42])
def test_invalid_backend_rejected_without_changing_projection(isolated_config, value):
    config.get_settings()
    before = isolated_config.read_text()
    with pytest.raises(HTTPException) as error:
        configurations._validate_updates({"shreckworld_embedding_backend": value})
    assert error.value.status_code == 400
    assert isolated_config.read_text() == before


def test_projection_failure_is_visible_and_reload_repairs_saved_selection(isolated_config, monkeypatch):
    config.get_settings()
    publish = config.publish_runtime_config
    def fail(*args):
        raise OSError("projection unavailable")
    monkeypatch.setattr(config, "publish_runtime_config", fail)
    with pytest.raises(OSError, match="projection unavailable"):
        config.update_settings({"shreckworld_embedding_backend": "openvino"})
    assert json.loads(isolated_config.read_text())["embedding_backend"] == "onnx"
    monkeypatch.setattr(config, "publish_runtime_config", publish)
    assert config.reload_settings().shreckworld_embedding_backend == "openvino"
    assert json.loads(isolated_config.read_text())["embedding_backend"] == "openvino"
