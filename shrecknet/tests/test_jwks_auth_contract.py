from datetime import datetime, timezone

from app.core.security import create_access_token, decode_access_token, jwks
from app.core.config import get_settings


def test_token_roundtrip_and_jwks_shape() -> None:
    token = create_access_token("1", "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    data = jwks()
    assert data["keys"][0]["kty"] == "RSA"
    assert data["keys"][0]["kid"]


def test_default_token_expiry_uses_configured_one_year_lifetime() -> None:
    settings = get_settings()
    token = create_access_token("1", "admin")
    payload = decode_access_token(token)
    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

    lifetime_minutes = round((expires_at - issued_at).total_seconds() / 60)
    assert lifetime_minutes == settings.jwt_access_token_expiry_minutes
