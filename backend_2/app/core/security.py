from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

# Some environments ship a bcrypt build that omits the __about__ attribute expected by passlib.
# When that happens passlib's backend loader raises AttributeError. Patch the module lazily here
# so hashing keeps working even with minimal bcrypt wheels.
try:  # pragma: no cover - environment specific
    import bcrypt
    from types import SimpleNamespace

    version = getattr(bcrypt, "__version__", "0")
    for module in (bcrypt, getattr(bcrypt, "_bcrypt", None)):
        if module is None:
            continue
        if not hasattr(module, "__about__"):
            module.__about__ = SimpleNamespace(__version__=version)
except Exception:  # pragma: no cover - best effort patching
    pass

_BCRYPT_MAX_BYTES = 72


def _normalise_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_BYTES:
        return password
    truncated = encoded[:_BCRYPT_MAX_BYTES]
    return truncated.decode("utf-8", "ignore")


from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt_sha256", "bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(_normalise_password(plain_password), hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(_normalise_password(password))


def create_access_token(
    subject: str | int, expires_delta: timedelta | None = None
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as exc:  # pragma: no cover - jose handles specifics
        raise ValueError("Invalid token") from exc
