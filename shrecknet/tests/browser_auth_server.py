"""Isolated browser-integration server; never reads/writes deployment databases.

From shrecknet/: PYTHONPATH=. python tests/browser_auth_server.py
Creates an ephemeral database and a development-only storyteller account.
"""
import os
import tempfile
from pathlib import Path

temporary = tempfile.TemporaryDirectory(prefix="shreckos-browser-test-")
os.environ["SHRECKNET_DATA_DIR"] = temporary.name
os.environ["SHRECKNET_DATABASE_URL"] = "sqlite:///" + str(Path(temporary.name) / "identity.db")
os.environ["SHRECKNET_MEDIA_ROOT"] = str(Path(temporary.name) / "media")

from cryptography.fernet import Fernet
os.environ["SHRECKNET_BROWSER_ENABLED"] = "true"
os.environ["SHRECKNET_BROWSER_SECRET"] = Fernet.generate_key().decode()
os.environ["SHRECKNET_BROWSER_ALLOWED_ORIGINS"] = '["http://127.0.0.1:5174"]'
os.environ["SHRECKNET_BROWSER_SECURE_COOKIES"] = "false"

from fastapi import FastAPI
import uvicorn
from app.api.routers import browser_auth
from app.core.config_store import update_settings, UserCreationMode
from app.core.security import get_password_hash
from app.db.init_db import init_db
from app.db.session import get_sessionmaker
from app.models.user import User, UserApprovalStatus, UserRole

init_db()
update_settings({"user_creation_mode": UserCreationMode.ALLOWED, "email_verification_enabled": False})
with get_sessionmaker()() as session:
    session.add(User(username="storyteller", email="story@example.com", full_name="Story Teller", timezone="Europe/Brussels", role=UserRole.PLAYER, approval_status=UserApprovalStatus.APPROVED, hashed_password=get_password_hash("a long memorable password")))
    session.commit()
app = FastAPI()
app.include_router(browser_auth.router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
