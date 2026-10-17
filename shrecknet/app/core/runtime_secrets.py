from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Literal

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

RuntimeSecretName = Literal[
    "browser_auth_secret",
    "jwt_private_key.pem",
    "jwt_public_key.pem",
]


def _secrets_dir() -> Path:
    return Path(os.getenv("SHRECKNET_RUNTIME_SECRETS_DIR", "/data/runtime-secrets"))


def read_runtime_secret(name: RuntimeSecretName) -> str:
    try:
        return (_secrets_dir() / name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_secret(path: Path, value: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        os.chmod(temporary_path, 0o600)
        temporary.write(value)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary_path, path)
    os.chmod(path, 0o600)
    directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def validate_jwt_keypair(
    private_pem: bytes | str,
    public_pem: bytes | str,
) -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_value = private_pem.encode() if isinstance(private_pem, str) else private_pem
    public_value = public_pem.encode() if isinstance(public_pem, str) else public_pem
    private_key = serialization.load_pem_private_key(private_value, password=None)
    public_key = serialization.load_pem_public_key(public_value)
    if not isinstance(private_key, RSAPrivateKey) or not isinstance(public_key, RSAPublicKey):
        raise ValueError("Shrecknet JWT signing keys must be RSA keys")
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise ValueError("Shrecknet JWT signing keys do not match")
    return private_key, public_key


def _load_keypair(private_path: Path, public_path: Path) -> tuple[RSAPrivateKey, RSAPublicKey]:
    return validate_jwt_keypair(private_path.read_bytes(), public_path.read_bytes())


def provision_runtime_secrets() -> None:
    import fcntl

    secrets_dir = _secrets_dir()
    secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o700)
    lock_path = secrets_dir / ".provision.lock"
    with lock_path.open("a+b") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        _provision_locked(secrets_dir)


def _provision_locked(secrets_dir: Path) -> None:
    browser_secret = os.getenv("SHRECKNET_BROWSER_SECRET", "").strip()
    if browser_secret:
        Fernet(browser_secret.encode("ascii"))
    else:
        secret_path = secrets_dir / "browser_auth_secret"
        if not secret_path.exists():
            _write_secret(secret_path, Fernet.generate_key())
        Fernet(secret_path.read_bytes().strip())
        os.chmod(secret_path, 0o600)

    private_env = os.getenv("SHRECKNET_JWT_PRIVATE_KEY_PEM", "").replace("\\n", "\n").strip()
    public_env = os.getenv("SHRECKNET_JWT_PUBLIC_KEY_PEM", "").replace("\\n", "\n").strip()
    if bool(private_env) != bool(public_env):
        raise RuntimeError("Configure both SHRECKNET_JWT_PRIVATE_KEY_PEM and SHRECKNET_JWT_PUBLIC_KEY_PEM, or neither")
    if private_env and public_env:
        validate_jwt_keypair(private_env, public_env)
        return

    private_path = secrets_dir / "jwt_private_key.pem"
    public_path = secrets_dir / "jwt_public_key.pem"
    if public_path.exists() and not private_path.exists():
        raise RuntimeError("Persisted Shrecknet JWT public key exists without its private key")
    if private_path.exists():
        private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(private_key, RSAPrivateKey):
            raise RuntimeError("Persisted Shrecknet JWT private key must be an RSA key")
        if not public_path.exists():
            public_value = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            _write_secret(public_path, public_value)
    else:
        private_key = serialization.load_pem_private_key(
            _generate_private_key(),
            password=None,
        )
        public_value = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        _write_secret(private_path, private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        _write_secret(public_path, public_value)
    _load_keypair(private_path, public_path)
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o600)


def _generate_private_key() -> bytes:
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


if __name__ == "__main__":
    provision_runtime_secrets()
    print("Persistent Shrecknet authentication secrets are ready.", flush=True)
