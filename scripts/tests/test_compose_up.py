from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compose_up


def _fake_docker(monkeypatch, *, gpu_available: bool):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "info"]:
            return SimpleNamespace(returncode=0)
        if command[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0 if gpu_available else 1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(compose_up.subprocess, "run", run)
    return commands


def test_launcher_uses_cuda_overlay_when_docker_can_access_gpu(tmp_path, monkeypatch):
    commands = _fake_docker(monkeypatch, gpu_available=True)

    assert compose_up.main(["--build"], root=tmp_path) == 0
    assert commands[-1] == [
        "docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml",
        "up", "--build",
    ]


def test_launcher_uses_cpu_stack_when_auto_parser_has_no_gpu(tmp_path, monkeypatch):
    commands = _fake_docker(monkeypatch, gpu_available=False)
    monkeypatch.setattr(compose_up, "ROOT", tmp_path)
    config_path = tmp_path / "shrecknet/databases/service-config/shreckworld.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"parser_device":"auto","embedding_device":"cpu"}')

    assert compose_up.main(["--build"], root=tmp_path) == 0
    assert commands[-1] == ["docker", "compose", "-f", "docker-compose.yml", "up", "--build"]


@pytest.mark.parametrize(
    ("runtime_config", "expected_setting"),
    [
        ('{"parser_device":"cuda","embedding_device":"cpu"}', "Parser device"),
        ('{"parser_device":"auto","embedding_device":"cuda"}', "Embedding device"),
    ],
)
def test_cpu_stack_refuses_explicit_cuda_settings(tmp_path, monkeypatch, runtime_config, expected_setting):
    commands = _fake_docker(monkeypatch, gpu_available=False)
    config_path = tmp_path / "shrecknet/databases/service-config/shreckworld.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(runtime_config)

    with pytest.raises(SystemExit, match=expected_setting):
        compose_up.main(["--build"], root=tmp_path)
    assert not any(command[:2] == ["docker", "compose"] for command in commands)


def test_mineru_profile_is_started_by_launcher_when_gpu_is_available(tmp_path, monkeypatch):
    commands = _fake_docker(monkeypatch, gpu_available=True)
    assert compose_up.main(["--build", "-d", "--mineru"], root=tmp_path) == 0
    assert commands[-1] == [
        "docker", "compose", "-f", "docker-compose.yml", "--profile", "mineru",
        "-f", "docker-compose.gpu.yml", "up", "--build", "-d",
    ]


def test_mineru_profile_requires_docker_gpu_access(tmp_path, monkeypatch):
    commands = _fake_docker(monkeypatch, gpu_available=False)
    with pytest.raises(SystemExit, match="--mineru option requires"):
        compose_up.main(["--build", "--mineru"], root=tmp_path)
    assert not any(command[:2] == ["docker", "compose"] for command in commands)
