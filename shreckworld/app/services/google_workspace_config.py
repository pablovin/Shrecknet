"""Installation-wide Google OAuth configuration, private to the service.

The SQLite data directory is shared by the API and workers. Environment
configuration remains authoritative for existing installations.
"""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.engine import make_url

CALLBACK_PATH = "/shreckworld-api/api/v1/shrecktables/google/oauth/callback"
RETURN_PATH = "/home/worlds/apps/%2Fapps%2Fgoogle-workspace"


def _directory(settings):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise HTTPException(503, "Configure Google OAuth using environment variables for this database")
    return Path(url.database).resolve().parent / ".google-workspace"


def _publish(path, data):
    """Atomic, non-overwriting publication; readers never see partial data."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temp)


def _key(settings, directory, create=False):
    if settings.google_token_fernet_key:
        return settings.google_token_fernet_key
    path = directory / "key"
    if create and not path.exists():
        _publish(path, Fernet.generate_key())
    return path.read_text().strip()


def resolve(settings):
    # Never replace existing credentials or reinterpret existing refresh tokens.
    if any((settings.google_client_id, settings.google_client_secret,
            settings.google_redirect_uri)):
        return settings, "environment"
    try:
        directory = _directory(settings)
    except HTTPException:
        return settings, "unconfigured"
    path = directory / "oauth.json"
    if not path.exists():
        return settings, "unconfigured"
    try:
        key = _key(settings, directory)
        payload = json.loads(Fernet(key.encode()).decrypt(path.read_bytes()))
        return settings.model_copy(update={
            **payload, "google_token_fernet_key": key,
        }), "wizard"
    except Exception as exc:
        raise HTTPException(503, "Saved Google OAuth configuration cannot be read") from exc


def configured(settings):
    if not all((settings.google_client_id, settings.google_client_secret,
                settings.google_redirect_uri, settings.google_token_fernet_key)):
        return False
    try:
        Fernet(settings.google_token_fernet_key.encode())
        return True
    except (ValueError, TypeError):
        return False


def save(settings, web, redirect_uri):
    current, source = resolve(settings)
    if source != "unconfigured":
        raise HTTPException(409, "OAuth already configured; existing credentials are preserved")
    parsed = urlsplit(redirect_uri)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})) or not parsed.hostname or parsed.username or parsed.password or parsed.path != CALLBACK_PATH or parsed.query or parsed.fragment:
        raise HTTPException(422, "Use the displayed HTTPS callback URL")
    client_id = web.get("client_id")
    secret = web.get("client_secret")
    redirects = web.get("redirect_uris")
    if (not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com")
        or len(client_id) > 512 or not isinstance(secret, str) or not secret or len(secret) > 512
        or not isinstance(redirects, list) or redirect_uri not in redirects):
        raise HTTPException(422, "Import a Google Web application JSON with the exact callback registered")
    directory = _directory(settings)
    try:
        key = _key(settings, directory, create=True)
        payload = {
            "google_client_id": client_id,
            "google_client_secret": secret,
            "google_redirect_uri": redirect_uri,
            "google_frontend_return_url": f"{parsed.scheme}://{parsed.netloc}{RETURN_PATH}",
        }
        if not _publish(directory / "oauth.json", Fernet(key.encode()).encrypt(json.dumps(payload).encode())):
            raise HTTPException(409, "OAuth already configured")
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Cannot persist Google OAuth configuration") from exc
    return resolve(settings)[0]
