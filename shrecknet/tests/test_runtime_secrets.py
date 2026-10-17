from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization

from app.core import runtime_secrets


def configure_secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    secrets_dir = tmp_path / "runtime-secrets"
    monkeypatch.setenv("SHRECKNET_RUNTIME_SECRETS_DIR", str(secrets_dir))
    for name in (
        "SHRECKNET_BROWSER_SECRET",
        "SHRECKNET_JWT_PRIVATE_KEY_PEM",
        "SHRECKNET_JWT_PUBLIC_KEY_PEM",
    ):
        monkeypatch.delenv(name, raising=False)
    return secrets_dir


def test_provisioned_authentication_secrets_are_valid_stable_and_private(tmp_path, monkeypatch):
    secrets_dir = configure_secrets_dir(tmp_path, monkeypatch)
    monkeypatch.setenv("SHRECKNET_PUBLIC_ORIGIN", "https://os.example")

    runtime_secrets.provision_runtime_secrets()
    original_values = {
        name: (secrets_dir / name).read_bytes()
        for name in ("browser_auth_secret", "jwt_private_key.pem", "jwt_public_key.pem")
    }
    runtime_secrets.provision_runtime_secrets()

    assert all(
        (secrets_dir / name).read_bytes() == value
        for name, value in original_values.items()
    )
    Fernet(original_values["browser_auth_secret"])
    private_key = serialization.load_pem_private_key(original_values["jwt_private_key.pem"], password=None)
    public_key = serialization.load_pem_public_key(original_values["jwt_public_key.pem"])
    assert private_key.public_key().public_numbers() == public_key.public_numbers()
    assert os.stat(secrets_dir).st_mode & 0o777 == 0o700
    assert all(os.stat(secrets_dir / name).st_mode & 0o777 == 0o600 for name in original_values)


def test_provision_recovers_public_key_from_persisted_private_key(tmp_path, monkeypatch):
    secrets_dir = configure_secrets_dir(tmp_path, monkeypatch)
    secrets_dir.mkdir()
    private_key = serialization.load_pem_private_key(
        runtime_secrets._generate_private_key(),
        password=None,
    )
    (secrets_dir / "jwt_private_key.pem").write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    original_private = (secrets_dir / "jwt_private_key.pem").read_bytes()

    runtime_secrets.provision_runtime_secrets()

    assert (secrets_dir / "jwt_private_key.pem").read_bytes() == original_private
    runtime_secrets._load_keypair(
        secrets_dir / "jwt_private_key.pem",
        secrets_dir / "jwt_public_key.pem",
    )


def test_provision_fails_for_public_key_without_private_key(tmp_path, monkeypatch):
    secrets_dir = configure_secrets_dir(tmp_path, monkeypatch)
    secrets_dir.mkdir()
    (secrets_dir / "jwt_public_key.pem").write_text("public key is not recoverable")

    with pytest.raises(RuntimeError, match="without its private key"):
        runtime_secrets.provision_runtime_secrets()


def test_provision_rejects_partial_external_jwt_keypair(tmp_path, monkeypatch):
    configure_secrets_dir(tmp_path, monkeypatch)
    monkeypatch.setenv("SHRECKNET_JWT_PRIVATE_KEY_PEM", "configured")

    with pytest.raises(RuntimeError, match="both SHRECKNET_JWT_PRIVATE_KEY_PEM"):
        runtime_secrets.provision_runtime_secrets()
