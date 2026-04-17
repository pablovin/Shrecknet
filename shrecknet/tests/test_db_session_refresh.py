from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

import app.db.session as session_module


def test_get_sessionmaker_calls_get_engine(monkeypatch):
    """Ensure SQLite fingerprint refresh path runs even when sessionmaker is cached."""
    # Provide a cached sessionmaker to exercise the exact regression path.
    session_module._sessionmaker = sessionmaker(class_=Session)

    called = {"count": 0}

    def _fake_get_engine():
        called["count"] += 1
        return None

    monkeypatch.setattr(session_module, "get_engine", _fake_get_engine)

    sm = session_module.get_sessionmaker()

    assert sm is session_module._sessionmaker
    assert called["count"] == 1
