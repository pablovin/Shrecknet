"""ShreckTables API regression tests with shared ShreckWorld test environment."""
from __future__ import annotations

from test_upload_metadata_api import api


def mock_contracts(monkeypatch):
    def fake(path, token):
        if path == "/contracts/worlds/world-1":
            return {"id": "world-1", "name": "Pendragon", "ontology_ids": [7]}
        if path.startswith("/contracts/users?"):
            return [{"id": int(value), "username": "player" + value, "full_name": "Player " + value, "avatar_url": None}
                    for value in path.split("?", 1)[1].split("&") if (value := value.split("=")[-1])]
        if path.startswith("/contracts/users/"):
            ident = path.rsplit("/", 1)[-1]
            return {"id": ident, "full_name": "Player " + ident, "role": "player", "email": "test@example.com"}
        raise AssertionError(path)
    monkeypatch.setattr("app.api.routers.shrecktables.shrecknet_read", fake)
    monkeypatch.setattr("app.api.routers.shrecktables.resolve_ontology",
                        lambda identifier, token: {"id": identifier})


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
    assert client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments/{file_id}").status_code == 401
    assert client.get(f"/api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/attachments",headers=headers).json()[0]["id"] == file_id
