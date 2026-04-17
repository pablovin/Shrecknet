from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

import app.core.config_store as config_store
import app.db.session as session_module


def _reset_session_state() -> None:
    session_module._reset_cached_engine()
    config_store.reload_settings()


def test_get_engine_reloads_when_sqlite_file_is_replaced(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "reload.db"
    monkeypatch.setenv("SHRECKNET_DATABASE_URL", f"sqlite:///{db_path}")
    _reset_session_state()

    engine1 = session_module.get_engine()
    with engine1.begin() as conn:
        conn.execute(text("create table sample (value integer not null)"))
        conn.execute(text("insert into sample(value) values (1)"))

    # Simulate a later request on the long-lived API process after the DB file exists.
    engine2 = session_module.get_engine()
    assert engine2 is not engine1

    engine2.dispose()
    db_path.unlink()

    engine3 = session_module.get_engine()
    assert engine3 is not engine2

    with engine3.begin() as conn:
        conn.execute(text("create table sample (value integer not null)"))
        conn.execute(text("insert into sample(value) values (2)"))

    engine4 = session_module.get_engine()
    assert engine4 is not engine3

    with engine4.connect() as conn:
        value = conn.execute(text("select value from sample")).scalar_one()
    assert value == 2

    _reset_session_state()
