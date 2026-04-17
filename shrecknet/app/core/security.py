from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import JWTError, jwt

from app.core.config_store import get_settings


class TokenError(ValueError):
    pass


_PRIVATE_KEY_PEM: str | None = None
_PUBLIC_KEY_PEM: str | None = None
_CONFIGURED_KEYPAIR: tuple[str, str] | None = None


def get_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return get_password_hash(plain_password) == (hashed_password or "")


def _resolve_keypair() -> tuple[str, str]:
    global _PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM, _CONFIGURED_KEYPAIR
    settings = get_settings()
    if settings.jwt_private_key_pem and settings.jwt_public_key_pem:
        configured = (settings.jwt_private_key_pem, settings.jwt_public_key_pem)
        if _CONFIGURED_KEYPAIR != configured:
            _PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM = configured
            _CONFIGURED_KEYPAIR = configured
        return _PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM

    if _PRIVATE_KEY_PEM and _PUBLIC_KEY_PEM:
        return _PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    _PRIVATE_KEY_PEM = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    _PUBLIC_KEY_PEM = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return _PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    private_key_pem, _ = _resolve_keypair()
    now = datetime.now(timezone.utc)
    expiry_minutes = expires_minutes or settings.jwt_access_token_expiry_minutes
    payload = {
        "sub": subject,
        "role": role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expiry_minutes)).timestamp()),
    }
    headers = {"kid": settings.jwt_kid, "typ": "JWT"}
    return jwt.encode(payload, private_key_pem, algorithm="RS256", headers=headers)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    _, public_key_pem = _resolve_keypair()
    try:
        return jwt.decode(
            token,
            public_key_pem,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError as exc:
        raise TokenError("invalid_token") from exc


def _b64url_uint(data: int) -> str:
    import base64

    raw = data.to_bytes((data.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwks() -> dict[str, list[dict[str, str]]]:
    settings = get_settings()
    _, public_key_pem = _resolve_keypair()
    pub = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    numbers = pub.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": settings.jwt_kid,
                "alg": "RS256",
                "use": "sig",
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }
