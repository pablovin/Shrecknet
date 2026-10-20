from pathlib import Path

import pytest
from pydantic import ValidationError
from shreckconfig import load_cors_policy


def test_policy_uses_only_file_without_environment_interpolation(tmp_path, monkeypatch):
    file = tmp_path / ".env"
    file.write_text('''CORS_ALLOW_ORIGINS=["https://table.example"]
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=["GET","POST"]
CORS_ALLOW_HEADERS=["Authorization","Content-Type"]
CORS_MAX_AGE=100
UNRELATED_SECRET=never-include
''')
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://untrusted.example"]')
    monkeypatch.setenv("SHRECKNET_CORS_ALLOW_ORIGINS", "invalid-json")
    monkeypatch.setenv("SHRECKWORLD_CORS_ALLOW_ORIGINS", "invalid-json")
    policy = load_cors_policy(file)
    assert policy.allow_origins == ["https://table.example"]
    assert policy.allow_credentials is True
    assert policy.max_age == 100
    assert "UNRELATED_SECRET" not in policy.model_dump()
    file.write_text('CORS_ALLOW_ORIGIN_REGEX=${RUNTIME_OVERRIDE}\n')
    monkeypatch.setenv("RUNTIME_OVERRIDE", ".*")
    assert load_cors_policy(file).allow_origin_regex == "${RUNTIME_OVERRIDE}"


def test_missing_file_denies_cross_origin_access(tmp_path):
    assert load_cors_policy(tmp_path / "missing").allow_origins == []


@pytest.mark.parametrize("value", ['not-json', '["*"]', '["https://table.example/path"]', '["https://user:secret@table.example"]', '["https://table.example/"]'])
def test_invalid_origins_fail_startup(tmp_path, value):
    file = tmp_path / ".env"
    file.write_text(f"CORS_ALLOW_ORIGINS={value}\n")
    with pytest.raises((ValueError, ValidationError)):
        load_cors_policy(file)


def test_changes_are_visible_on_reload(tmp_path):
    file = tmp_path / ".env"
    file.write_text('CORS_ALLOW_ORIGINS=["https://first.example"]\n')
    assert load_cors_policy(file).allow_origins == ["https://first.example"]
    file.write_text('CORS_ALLOW_ORIGINS=["https://second.example"]\n')
    assert load_cors_policy(file).allow_origins == ["https://second.example"]


def test_container_cors_mount_does_not_nest_inside_read_only_config_mount():
    import yaml

    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    for service in ("shrecknet_api", "shreckworld_api"):
        mounts = compose["services"][service]["volumes"]
        cors_mount = next(mount for mount in mounts if isinstance(mount, dict)
                          and mount.get("source") == "./.env")
        target = Path(cors_mount["target"])
        assert str(target) == "/app/browser.env"
        assert cors_mount["read_only"] is True
        assert cors_mount["bind"]["create_host_path"] is False
        for mount in mounts:
            if isinstance(mount, str):
                parts = mount.split(":")
                parent, readonly = Path(parts[1]), len(parts) > 2 and "ro" in parts[2].split(",")
            else:
                if mount is cors_mount:
                    continue
                parent, readonly = Path(mount["target"]), mount.get("read_only", False)
            assert not (readonly and parent in target.parents), f"{service}: {target} is beneath read-only {parent}"


def test_loader_uses_the_container_mount(monkeypatch):
    from shreckconfig import cors_file
    monkeypatch.setattr(Path, "exists", lambda path: str(path) == "/app/browser.env")
    assert cors_file() == Path("/app/browser.env")
