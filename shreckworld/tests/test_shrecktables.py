"""ShreckTables API regression tests with shared ShreckWorld test environment."""
from __future__ import annotations

from test_upload_metadata_api import api


def mock_contracts(monkeypatch):
    def fake(path, token):
        if path == "/contracts/worlds/world-1":
            return {"id": "world-1", "name": "Pendragon", "ontology_ids": [7], "active_ontology_id": 7}
        if path == "/contracts/worlds/world-2":
            return {"id": "world-2", "name": "Sartar", "ontology_ids": [8], "active_ontology_id": 8}
        if path == "/contracts/worlds/ambiguous":
            return {"id": "ambiguous", "name": "Legacy", "ontology_ids": [7, 8], "active_ontology_id": None}
        if path in {"/ontologies/7", "/ontologies/8"}:
            return {"id": int(path.rsplit("/", 1)[-1]), "name": "World ontology", "description": None}
        if path.startswith("/contracts/users?"):
            return [{"id": int(value), "username": "player" + value, "full_name": "Player " + value, "avatar_url": None}
                    for pair in path.split("?", 1)[1].split("&") for value in [pair.split("=")[-1]]]
        if path.startswith("/contracts/users/"):
            ident = path.rsplit("/", 1)[-1]
            return {"id": ident, "full_name": "Player " + ident, "role": "player", "email": "test@example.com"}
        raise AssertionError(path)
    monkeypatch.setattr("app.api.routers.shrecktables.shrecknet_read", fake)
    monkeypatch.setattr("app.integrations.worlds.shrecknet_read", fake)
    monkeypatch.setattr("app.integrations.ontologies.shrecknet_read", fake)


def make_table(client, headers):
    result = client.post("/api/v1/shrecktables/tables", headers=headers,
        json={"world_id": "world-1", "ontology_id": 7, "name": "Winter Court", "timezone": "Europe/Brussels"})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_table_lifecycle_and_multi_gm(api, monkeypatch):
    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    assert client.get("/api/v1/shrecktables/tables", headers=headers).json()[0]["id"] == table_id
    new = client.post(f"/api/v1/shrecktables/tables/{table_id}/members", headers=headers,
                      json={"user_id": "2", "role": "player"})
    assert new.status_code == 201, new.text
    promoted = client.patch(f"/api/v1/shrecktables/tables/{table_id}/members/2?role=gm", headers=headers)
    assert promoted.status_code == 200
    demoted = client.patch(f"/api/v1/shrecktables/tables/{table_id}/members/2?role=player", headers=headers)
    assert demoted.status_code == 200
    assert client.patch(f"/api/v1/shrecktables/tables/{table_id}/members/1?role=player", headers=headers).status_code == 409
    table = client.get(f"/api/v1/shrecktables/tables/{table_id}", headers=headers).json()
    stale = client.patch(f"/api/v1/shrecktables/tables/{table_id}", headers={**headers,"If-Match":"1"},
                         json={"name":"New title"})
    assert stale.status_code == 409
    update = client.patch(f"/api/v1/shrecktables/tables/{table_id}", headers={**headers,"If-Match":str(table["revision"])},
                          json={"name": "The Winter Court"})
    assert update.status_code == 200
    assert update.json()["name"] == "The Winter Court"
    roster = client.get(f"/api/v1/shrecktables/tables/{table_id}/members", headers=headers)
    assert roster.status_code == 200
    assert any(user["full_name"] == "Player 2" for user in roster.json())


def test_sessions_poll_vote_finalize_and_attendance(api, monkeypatch):
    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    response = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions",headers=headers,
      json={"title":"Feast of the Round Table","scheduled_timezone":"Europe/Brussels"})
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    poll = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/polls",headers=headers,
      json={"timezone":"Europe/Brussels","options":["2027-01-12T19:00:00","2027-01-19T19:00:00"]})
    assert poll.status_code == 201, poll.text
    poll_id = poll.json()["id"]
    option = poll.json()["options"][0]["id"]
    voted = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/vote",
        headers=headers,json={"option_id":option})
    assert voted.status_code == 200, voted.text
    assert voted.json()["options"][0]["vote_count"] == 1
    finalized = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/finalize",
        headers=headers,json={"option_id":option})
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["scheduled_at_utc"].startswith("2027-01-12T18:00")
    assert client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/vote",
        headers=headers,json={"option_id":option}).status_code == 409
    attending = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attendance",
        headers=headers,json={"attending":True})
    assert attending.status_code == 200, attending.text
    assert attending.json()["attendance"][0]["attending"] is True
    listing = client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions?view=upcoming", headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 1


def test_session_attachments_are_private_and_correctly_scoped(api, monkeypatch):
    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    session_id = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions",headers=headers,
      json={"title":"A session"}).json()["id"]
    uploaded = client.post(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments",
       headers=headers,files={"upload": ("notes.txt", b"The mysterious forest", "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    download = client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments/{file_id}",headers=headers)
    assert download.status_code == 200 and download.content == b"The mysterious forest"
    from app.main import app
    from app.api.deps import get_current_user
    app.dependency_overrides.pop(get_current_user)
    assert client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments/{file_id}").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: __import__("app.api.deps", fromlist=["ShrecknetPrincipal"]).ShrecknetPrincipal(user_id="1", role="admin")
    assert client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments",headers=headers).json()[0]["id"] == file_id


def test_legacy_import_is_idempotent_and_does_not_recreate_google_events(api):
    import sqlite3
    from app.db.session import session_factory
    from app.models.shrecktables import Table, TableSession, TablePoll, TablePollOption, TablePollVote
    from scripts.migrate_shrecktables import Importer
    from sqlalchemy import select, func

    rpg = sqlite3.connect(":memory:")
    rpg.row_factory = sqlite3.Row
    try:
        rpg.executescript("""
            CREATE TABLE games (id INTEGER, ontology_id INTEGER, owner_user_id INTEGER,
                                name TEXT, google_calendar_id TEXT, vtt TEXT);
            CREATE TABLE game_memberships (id INTEGER, game_id INTEGER, user_id INTEGER, role TEXT);
            CREATE TABLE game_sessions (id INTEGER, game_id INTEGER, title TEXT, scheduled_date TEXT,
                   scheduled_timezone TEXT, google_event_id TEXT, google_meet_link TEXT, location TEXT, summary TEXT);
            CREATE TABLE game_session_attendance (id INTEGER, session_id INTEGER, user_id INTEGER, attending INTEGER, responded_at TEXT);
            CREATE TABLE game_session_polls (id INTEGER, session_id INTEGER, is_finalized INTEGER, finalized_option_id INTEGER);
            CREATE TABLE game_session_poll_options (id INTEGER, poll_id INTEGER, proposed_start TEXT);
            CREATE TABLE game_session_poll_votes (id INTEGER, option_id INTEGER, user_id INTEGER);
            INSERT INTO games VALUES (12, 7, 1, 'Cthulhu', 'original-calendar', NULL);
            INSERT INTO game_memberships VALUES (1, 12, 2, 'member');
            INSERT INTO game_sessions VALUES (41, 12, 'First case', '2027-02-09T19:00:00+00:00',
                  'Europe/Brussels', 'existing-google-event', 'https://meet.google.com/abc-defg-hij', NULL, NULL);
            INSERT INTO game_session_attendance VALUES (1, 41, 2, 1, '2027-01-01T12:00:00Z');
            INSERT INTO game_session_polls VALUES (5, 41, 1, 8);
            INSERT INTO game_session_poll_options VALUES (8, 5, '2027-02-09T19:00:00+00:00');
            INSERT INTO game_session_poll_votes VALUES (2, 8, 2);
        """)
        with session_factory()() as db:
            first = Importer(db, {"7": "canonical-world"}, apply=True)
            first.import_rpg(rpg)
            first.import_engine()
            db.commit()
        with session_factory()() as db:
            repeated = Importer(db, {"7": "canonical-world"}, apply=True)
            repeated.import_rpg(rpg)
            repeated.import_engine()
            db.commit()
            tables = db.scalars(select(Table)).all()
            sessions = db.scalars(select(TableSession)).all()
            polls = db.scalars(select(TablePoll)).all()
            assert len(tables) == len(sessions) == len(polls) == 1
            assert db.scalar(select(func.count()).select_from(TablePollOption)) == 1
            assert db.scalar(select(func.count()).select_from(TablePollVote)) == 1
            assert sessions[0].google_event_id == "existing-google-event"
            assert sessions[0].google_meet_url == "https://meet.google.com/abc-defg-hij"
            assert polls[0].finalized_option_id is not None
            assert tables[0].calendar_id == "original-calendar"
    finally:
        rpg.close()


def test_world_rules_and_narratives_are_shared_by_tables(api, monkeypatch):
    from app.api.deps import ShrecknetPrincipal, get_current_user
    from app.db.session import session_factory
    from app.main import app
    from app.models.game_engine import GameBinding, GameRuleset, GameRulesetVersion
    from app.models.shreckworld import ShreckWorldLibraryItem

    mock_contracts(monkeypatch)
    client, headers, _ = api
    tables = "/api/v1/shrecktables/tables"
    first = make_table(client, headers)
    new = client.post(tables, headers=headers, json={"world_id": "world-1", "name": "Second table"})
    assert new.status_code == 201, new.text
    second = new.json()["id"]
    other = client.post(tables, headers=headers, json={"world_id": "world-2", "name": "Other world"})
    assert other.status_code == 201, other.text
    assert other.json()["ontology_id"] == 8
    assert client.post(tables, headers=headers, json={
        "world_id": "world-1", "ontology_id": 8, "name": "Invalid scope",
    }).status_code == 409
    assert client.post(tables, headers=headers, json={
        "world_id": "ambiguous", "ontology_id": 7, "name": "Ambiguous legacy scope",
    }).status_code == 409

    with session_factory()() as db:
        db.add(GameRuleset(id="rules", name="Pendragon", edition="6e", schema_version="1",
                           status="published", created_by="1"))
        db.flush()
        db.add(GameBinding(ontology_id=7, ruleset_id="rules", created_by="1"))
        db.add(GameRulesetVersion(id="version", ruleset_id="rules", definition_revision=1,
                                  schema_version="1", definition_digest="digest",
                                  definitions_snapshot=[], published_by="1"))
        db.commit()

    rules = "/api/v1/game-engine/worlds"
    update = {"version_id": "version", "expected_revision": 0}
    assert client.put(f"{rules}/world-2/rules", headers=headers, json=update).status_code == 409
    selection = client.put(f"{rules}/world-1/rules", headers=headers, json=update)
    assert selection.status_code == 200, selection.text
    assert client.put(f"{rules}/world-1/rules", headers=headers, json=update).status_code == 409
    first_world = client.get(f"{tables}/{first}/world", headers=headers).json()
    assert first_world == client.get(f"{tables}/{second}/world", headers=headers).json()
    assert first_world["ontology_id"] == 7
    assert first_world["rules"]["version_id"] == "version"
    assert client.get(f"{tables}/{first}", headers=headers).json()["version_id"] == "version"
    assert client.get(f"{tables}/{other.json()['id']}", headers=headers).json()["version_id"] is None
    assert client.post(tables, headers=headers, json={
        "world_id": "world-1", "version_id": "local-rules", "name": "Invalid rules",
    }).status_code == 409

    source_ids = []
    for table_id, title in [(first, "The winter feast"), (second, "The spring hunt")]:
        entry = client.post(f"{tables}/{table_id}/sessions", headers=headers, json={"title": title})
        assert entry.status_code == 201, entry.text
        session_id = entry.json()["id"]
        endpoint = f"{tables}/{table_id}/sessions/{session_id}/narrative-source"
        reviewed = {"title": title, "narrative": "The knights returned to Camelot."}
        published = client.post(endpoint, headers=headers, json=reviewed)
        assert published.status_code == 201, published.text
        assert published.json()["ontology_id"] == 7
        source_ids.append(published.json()["source_id"])
        assert client.post(endpoint, headers=headers, json=reviewed).status_code == 409
        assert client.post(f"{tables}/{other.json()['id']}/sessions/{session_id}/narrative-source",
                           headers=headers, json=reviewed).status_code == 404

    for table_id in (first, second):
        shared = client.get(f"{tables}/{table_id}/world", headers=headers).json()
        collection = client.get(f"/ontologies/{shared['ontology_id']}/items", headers=headers)
        assert collection.status_code == 200, collection.text
        assert {source["id"] for source in collection.json()} == set(source_ids)
    assert client.get("/ontologies/8/items", headers=headers).json() == []

    with session_factory()() as db:
        from pathlib import Path

        for source_id in source_ids:
            source = db.get(ShreckWorldLibraryItem, source_id)
            assert source.ontology_id == 7 and source.source_type == "narrative"
            assert "The knights returned to Camelot." in Path(source.storage_path).read_text()

    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="2", role="player")
    assert client.get(f"{tables}/{second}/world", headers=headers).status_code == 403
    assert client.get(f"{tables}?world_id=world-1", headers=headers).json() == []


def test_world_integrity_detects_foreign_table_and_keeps_shared_sources(api, monkeypatch):
    from app.db.session import session_factory
    from app.models.shrecktables import Table

    mock_contracts(monkeypatch)
    client, headers, _ = api
    first = make_table(client, headers)
    second = make_table(client, headers)
    url = "/api/v1/shrecktables/worlds/world-1/knowledge-integrity"
    report = client.get(url, headers=headers)
    assert report.status_code == 200, report.text
    assert report.json()["consistent"] is True
    assert report.json()["table_count"] == 2
    assert report.json()["ontology_id"] == 7
    assert report.json()["graph_and_vector_store_verified"] is False

    # Simulate a pre-migration table claiming this world but using another
    # ontology; diagnostics must identify it without changing any record.
    with session_factory()() as db:
        row = db.get(Table, second)
        row.ontology_id = 8
        db.commit()
    broken = client.get(url, headers=headers)
    assert broken.status_code == 200, broken.text
    assert broken.json()["consistent"] is False
    assert {"code": "table_ontology_mismatch", "table_id": second} in broken.json()["issues"]

    # A table with this ontology but a different World is also a violation.
    with session_factory()() as db:
        row = db.get(Table, first)
        row.world_id = "world-2"
        db.commit()
    broken = client.get(url, headers=headers)
    assert {"code": "ontology_used_by_other_world", "table_id": first} in broken.json()["issues"]


def test_google_workspace_setup_route_and_safe_status(api, monkeypatch, tmp_path):
    from app.core.config import Settings
    from app.services.google_workspace_config import CALLBACK_PATH
    from app.main import app
    from app.api.deps import ShrecknetPrincipal, get_current_user

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'oauth-test' / 'world.db'}",
                        google_client_id="", google_client_secret="", google_redirect_uri="",
                        google_token_fernet_key="", google_frontend_return_url="")
    monkeypatch.setattr("app.api.routers.shrecktables_google.get_settings", lambda: settings)
    client, headers, _ = api
    path = "/api/v1/shrecktables/google/setup"
    assert {"get", "post"} <= set(client.get("/openapi.json").json()["paths"][path])
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False
    assert response.json()["source"] == "unconfigured"
    redirect = "https://example.test" + CALLBACK_PATH
    payload = {"redirect_uri": redirect, "web": {
        "client_id": "test.apps.googleusercontent.com",
        "client_secret": "fixture-secret", "redirect_uris": [redirect]}}
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="2", role="player")
    assert client.post(path, headers=headers, json=payload).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="1", role="admin")
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["configured"] is True
    assert result.json()["redirect_uri"] == redirect
    assert "fixture-secret" not in result.text
    assert client.get(path, headers=headers).json()["source"] == "wizard"
    assert client.post(path, headers=headers, json=payload).status_code == 409


def test_calendar_unlink_preserves_old_event_and_prevents_cross_calendar_sync(api, monkeypatch):
    from app.db.session import session_factory
    from app.models.shrecktables import Table, TableSession
    from app.services import shrecktables_google as google
    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    with session_factory()() as db:
        table = db.get(Table, table_id)
        table.calendar_id, table.google_connection_id = "old-calendar", "old-connection"
        row = TableSession(table_id=table_id, title="Existing", created_by="1", google_event_id="old-event")
        db.add(row); db.commit()
        session_id = row.id
    path = f"/api/v1/shrecktables/tables/{table_id}/calendar"
    assert client.delete(path, headers=headers).json() == {"status": "unlinked"}
    with session_factory()() as db:
        table = db.get(Table, table_id)
        row = db.get(TableSession, session_id)
        assert table.calendar_id is None and table.google_connection_id is None
        assert row.google_event_id == "old-event" and row.google_event_calendar_id == "old-calendar"
        table.calendar_id, table.google_connection_id = "new-calendar", "new-connection"
        db.commit()
        assert google.sync_event(db, session_id) == "detached"
        assert db.get(TableSession, session_id).google_event_id == "old-event"


def test_calendar_picker_excludes_primary_readonly_and_keeps_pagination(monkeypatch):
    from app.services import shrecktables_google as google
    monkeypatch.setattr(google, "access_token", lambda _: "token")
    monkeypatch.setattr(google, "google", lambda *args, **kwargs: {
        "items": [{"id":"primary", "primary":True, "accessRole":"owner"},
                  {"id":"readonly", "accessRole":"reader"},
                  {"id":"campaign", "summary":"Campaign", "accessRole":"writer"}],
        "nextPageToken":"next"})
    page = google.calendar_choices(None)
    assert [row["id"] for row in page["results"]] == ["campaign"]
    assert page["next_page_token"] == "next"


def test_calendar_event_projection_masks_private_events_and_expands_recurring(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.services import shrecktables_google as google
    calls = []
    def request(method, url, token, **kwargs):
        calls.append((method, url, kwargs))
        return {"items": [
            {"id":"e1", "summary":"Secret", "visibility":"private",
             "start":{"date":"2026-11-27"}, "end":{"date":"2026-11-28"}},
            {"id":"e2", "summary":"Campaign", "start":{"dateTime":"2026-11-27T20:00:00Z"},
             "end":{"dateTime":"2026-11-27T23:00:00Z"}},
            {"id":"cancelled", "status":"cancelled"}
        ], "nextPageToken":"next"}
    class DB:
        def scalars(self, statement):
            return SimpleNamespace(all=lambda: [SimpleNamespace(google_event_id="e2", id=17)])
    monkeypatch.setattr(google, "access_token", lambda _: "token")
    monkeypatch.setattr(google, "google", request)
    result = google.calendar_events(DB(), SimpleNamespace(id=1, calendar_id="calendar", timezone="UTC"), None,
                                    datetime(2026,11,1,tzinfo=timezone.utc), datetime(2026,12,1,tzinfo=timezone.utc))
    assert result["next_page_token"] == "next"
    assert result["results"][0]["title"] == "Private event"
    assert result["results"][0]["all_day"] is True
    assert result["results"][1]["session_id"] == 17
    assert len(result["results"]) == 2
    assert calls[0][2]["params"]["singleEvents"] is True


def test_meet_settings_are_independent_when_google_rejects_one(monkeypatch):
    from app.services import shrecktables_google as google
    import httpx
    calls = []
    def request(method, url, token, **kwargs):
        calls.append((method, kwargs))
        if kwargs["params"]["updateMask"] == "config.accessType":
            req = httpx.Request(method, url)
            raise httpx.HTTPStatusError("Bad request", request=req,
                response=httpx.Response(400, request=req))
        return {"name": "spaces/test"}
    monkeypatch.setattr(google, "google", request)
    assert google._configure_auto_artifacts("token", "spaces/test") == ["accessType (HTTP 400)"]
    assert len(calls) == 4
    configs = [call[1]["body"]["config"] for call in calls]
    assert configs[0] == {"accessType": "OPEN"}
    assert configs[1] == {"entryPointAccess": "ALL"}
    assert configs[2]["artifactConfig"]["transcriptionConfig"]["autoTranscriptionGeneration"] == "ON"
    assert configs[3]["artifactConfig"]["smartNotesConfig"]["autoSmartNotesGeneration"] == "ON"


def test_meet_link_uses_video_entry_point_and_ignores_non_meet_urls():
    from app.services import shrecktables_google as google
    assert google._meet_link({"hangoutLink": "https://example.com",
        "conferenceData": {"entryPoints": [
            {"entryPointType": "phone", "uri": "tel:123"},
            {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]}}) == "https://meet.google.com/abc-defg-hij"
    assert google._meet_link({"conferenceData": {"createRequest": {"status": {"statusCode": "pending"}}}}) is None


def test_table_current_session_survives_start(api, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.db.session import session_factory
    from app.models.shrecktables import TableSession
    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    now = datetime(2026, 11, 27, 20, tzinfo=timezone.utc)
    monkeypatch.setattr("app.api.routers.shrecktables.utcnow", lambda: now)
    with session_factory()() as db:
        active = TableSession(table_id=table_id, title="Playing", created_by="1", status="scheduled",
            scheduled_at_utc=now-timedelta(minutes=60), duration_minutes=180)
        future = TableSession(table_id=table_id, title="Next", created_by="1", status="scheduled",
            scheduled_at_utc=now+timedelta(days=7), duration_minutes=180)
        db.add_all([active,future]); db.commit()
        active_id, future_id = active.id, future.id
    row = client.get(f"/api/v1/shrecktables/tables/{table_id}", headers=headers).json()
    assert row["current_session"]["id"] == active_id
    assert row["current_session"]["duration_minutes"] == 180
    assert row["next_session"]["id"] == future_id
    monkeypatch.setattr("app.api.routers.shrecktables.utcnow", lambda: now+timedelta(minutes=120))
    assert client.get(f"/api/v1/shrecktables/tables/{table_id}", headers=headers).json()["current_session"] is None


def test_calendar_events_route_for_connected_table(api, monkeypatch):
    from app.db.session import session_factory
    from app.models.shrecktables import Table, GoogleConnection
    from app.services import shrecktables_google as google
    import httpx

    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    with session_factory()() as db:
        db.add(GoogleConnection(id="test-calendar-connection", owner_user_id="1",
            email="organizer@example.com", encrypted_refresh_token="unused",
            scopes="", status="connected"))
        table = db.get(Table, table_id)
        table.calendar_id = "campaign@group.calendar.google.com"
        table.google_connection_id = "test-calendar-connection"
        db.commit()

    calls = []
    def events(db, table, connection, start, end, page_token, *, organizer):
        calls.append((table.calendar_id, connection.id, organizer))
        return {"results": [], "next_page_token": None}
    monkeypatch.setattr(google, "calendar_events", events)
    path = f"/api/v1/shrecktables/tables/{table_id}/calendar/events"
    params = {"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"}
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.json() == {"results": [], "next_page_token": None}
    assert calls == [("campaign@group.calendar.google.com", "test-calendar-connection", True)]

    def missing(*args, **kwargs):
        request = httpx.Request("GET", "https://www.googleapis.com/calendar/v3/calendars/missing/events")
        raise httpx.HTTPStatusError("Not found", request=request,
            response=httpx.Response(404, request=request))
    monkeypatch.setattr(google, "calendar_events", missing)
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "google_calendar_not_accessible"


def test_create_calendar_requires_public_acl_before_link(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.services import shrecktables_google as google

    connection = SimpleNamespace(id="organizer", scopes="https://www.googleapis.com/auth/calendar.acls")
    table = SimpleNamespace(name="NicoQuest", timezone="UTC", calendar_id=None,
                            google_connection_id=None, revision=1)
    db = Mock()
    monkeypatch.setattr(google, "access_token", lambda _: "token")
    calls = []
    def request(method, url, token, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/calendars"):
            return {"id": "new@group.calendar.google.com"}
        assert table.calendar_id is None
        return {"role": "reader", "scope": {"type": "default"}}
    monkeypatch.setattr(google, "google", request)
    assert google.create_calendar(db, table, connection) == "new@group.calendar.google.com"
    assert [call[0] for call in calls] == ["POST", "POST", "GET"]
    assert calls[1][2]["body"] == {"role": "reader", "scope": {"type": "default"}}
    assert table.calendar_id == "new@group.calendar.google.com"
    db.commit.assert_called_once()


def test_create_calendar_does_not_create_without_sharing_scope(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fastapi import HTTPException
    import pytest
    from app.services import shrecktables_google as google

    token = Mock()
    monkeypatch.setattr(google, "access_token", token)
    with pytest.raises(HTTPException) as exc:
        google.create_calendar(Mock(), Mock(), SimpleNamespace(scopes=""))
    assert exc.value.detail["code"] == "google_calendar_sharing_reauthorization"
    token.assert_not_called()


def test_public_calendar_creation_fails_if_workspace_downgrades_acl(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fastapi import HTTPException
    import pytest
    from app.services import shrecktables_google as google

    table = SimpleNamespace(name="NicoQuest", timezone="UTC", calendar_id=None,
                            google_connection_id=None, revision=1)
    connection = SimpleNamespace(id="organizer", scopes="https://www.googleapis.com/auth/calendar.acls")
    db = Mock()
    monkeypatch.setattr(google, "access_token", lambda _: "token")
    request = Mock(side_effect=[{"id": "new-calendar"}, {},
                                {"role": "freeBusyReader", "scope": {"type": "default"}}, {}])
    monkeypatch.setattr(google, "google", request)
    with pytest.raises(HTTPException) as exc:
        google.create_calendar(db, table, connection)
    assert exc.value.detail["code"] == "google_calendar_public_sharing_blocked"
    assert table.calendar_id is None
    db.commit.assert_not_called()
    assert request.call_args.args[0] == "DELETE"


def test_public_repair_never_changes_primary_calendar(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fastapi import HTTPException
    import pytest
    from app.services import shrecktables_google as google

    monkeypatch.setattr(google, "access_token", lambda _: "token")
    request = Mock(return_value={"primary": True, "accessRole": "owner"})
    monkeypatch.setattr(google, "google", request)
    with pytest.raises(HTTPException) as exc:
        google.make_linked_calendar_public(SimpleNamespace(calendar_id="primary"),
            SimpleNamespace(scopes="https://www.googleapis.com/auth/calendar.acls"))
    assert exc.value.status_code == 403
    request.assert_called_once()


def test_sync_persists_meet_link_and_space_when_settings_fail(api, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.db.session import session_factory
    from app.models.shrecktables import Table, TableSession, GoogleConnection
    from app.services import shrecktables_google as google

    mock_contracts(monkeypatch)
    client, headers, _ = api
    table_id = make_table(client, headers)
    with session_factory()() as db:
        db.add(GoogleConnection(id="meet-test-connection", owner_user_id="1",
            email="organizer@example.com", encrypted_refresh_token="unused", scopes="", status="connected"))
        table = db.get(Table, table_id)
        table.calendar_id = "campaign@group.calendar.google.com"
        table.google_connection_id = "meet-test-connection"
        row = TableSession(table_id=table_id, title="Playing", created_by="1", status="scheduled",
            scheduled_at_utc=datetime.now(timezone.utc)+timedelta(days=1), duration_minutes=180)
        db.add(row); db.commit()
        session_id = row.id
    monkeypatch.setattr(google, "access_token", lambda _: "token")
    monkeypatch.setattr(google, "google", lambda *args, **kwargs: {"hangoutLink": "https://meet.google.com/abc-defg-hij"})
    monkeypatch.setattr(google, "_resolve_meet_space", lambda *args: "spaces/test")
    monkeypatch.setattr(google, "_configure_auto_artifacts", lambda *args: ["smart notes (HTTP 400)"])
    with session_factory()() as db:
        assert google.sync_event(db, session_id) == "partial"
    with session_factory()() as db:
        row = db.get(TableSession, session_id)
        assert row.google_event_id
        assert row.google_meet_url == "https://meet.google.com/abc-defg-hij"
        assert row.meet_space == "spaces/test"
        assert "smart notes (HTTP 400)" in row.calendar_sync_error
    response = client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["google_meet_link"] == "https://meet.google.com/abc-defg-hij"
    assert response.json()["calendar_sync_status"] == "partial"

    # A subsequent sync must reuse the existing event rather than insert again.
    calls = []
    def request(method, url, token, **kwargs):
        calls.append((method, url))
        return {"conferenceData": {"createRequest": {"status": {"statusCode": "pending"}}}}
    monkeypatch.setattr(google, "google", request)
    with session_factory()() as db:
        assert google.sync_event(db, session_id) == "partial"
        assert db.get(TableSession, session_id).google_event_id
    assert [method for method, _ in calls] == ["PATCH", "GET"]


    # Editing and cancelling through the public ShreckTables endpoints must
    # enqueue synchronization and operate on the original Google event.
    queued = []
    monkeypatch.setattr("app.api.routers.shrecktables._enqueue_sync",
                        lambda db, row: queued.append(row.id))
    event_path = f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}"
    with session_factory()() as db:
        event_id = db.get(TableSession, session_id).google_event_id
    new_date = datetime.now(timezone.utc)+timedelta(days=7)
    response = client.patch(event_path, headers=headers, json={
        "title": "Rescheduled", "scheduled_at": new_date.isoformat(),
        "scheduled_timezone": "UTC", "duration_minutes": 180,
    })
    assert response.status_code == 200, response.text
    assert response.json()["calendar_sync_status"] == "pending"
    calls.clear()
    def reschedule(method, url, token, **kwargs):
        calls.append((method, url, kwargs))
        return {"hangoutLink": "https://meet.google.com/abc-defg-hij"}
    monkeypatch.setattr(google, "google", reschedule)
    monkeypatch.setattr(google, "_configure_auto_artifacts", lambda *args: [])
    with session_factory()() as db:
        assert google.sync_event(db, session_id) == "synced"
        assert db.get(TableSession, session_id).google_event_id == event_id
    assert len(calls) == 1
    assert calls[0][0] == "PATCH" and calls[0][1].endswith("/"+event_id)
    assert datetime.fromisoformat(calls[0][2]["body"]["start"]["dateTime"]) == new_date
    assert "conferenceData" not in calls[0][2]["body"]

    assert client.delete(event_path, headers=headers).status_code == 204
    assert queued == [session_id, session_id]
    calls.clear()
    with session_factory()() as db:
        assert google.sync_event(db, session_id) == "not_configured"
        row = db.get(TableSession, session_id)
        assert row.status == "cancelled" and row.google_event_id is None
    assert len(calls) == 1
    assert calls[0][0] == "DELETE" and calls[0][1].endswith("/"+event_id)
