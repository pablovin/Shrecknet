from pathlib import Path

import pytest

from test_document_metadata import docx_bytes, pdf_bytes
from test_shreckworld_api import _client


@pytest.fixture
def api(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)
    world = client.post("/admin/shreckworlds", headers=headers, json={"name": "Books", "shrecknet_world_id": "books"}).json()
    yield client, headers, world["id"]
    from app.main import app
    app.dependency_overrides.clear()
    client.close()


def upload(api, filename="book.pdf", raw=None, **kwargs):
    client, headers, world_id = api
    return client.post(f"/admin/shreckworlds/{world_id}/library-items", headers=headers,
                       files={"pdf": (filename, raw if raw is not None else pdf_bytes({"/Title": "Book", "/Author": "Writer", "/Subject": "Summary"}))}, **kwargs)


def test_pdf_autofill_and_authenticated_cover(api):
    client, headers, world_id = api
    response = upload(api, data={"auto_extract_metadata": "true"})
    assert response.status_code == 201, response.text
    item = response.json()
    assert (item["title"], item["authors"], item["description"]) == ("Book", "Writer", "Summary")
    assert item["cover_url"] == f"/shreckworlds/{world_id}/library-items/{item['id']}/cover"
    cover = client.get(item["cover_url"], headers=headers)
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/png"
    assert cover.content.startswith(b"\x89PNG")
    assert item["index_status"] == "unindexed"
    # Cover lookup must verify the item's world, just like source download.
    other = client.post("/admin/shreckworlds", headers=headers, json={"name": "Other", "shrecknet_world_id": "other"}).json()
    assert client.get(item["cover_url"].replace(world_id, other["id"]), headers=headers).status_code == 404
    from app.main import app
    from app.api.deps import get_current_user
    app.dependency_overrides.pop(get_current_user)
    assert client.get(item["cover_url"]).status_code == 401


def test_form_overrides_query_and_extracted_fields(api):
    response = upload(api, params={"title": "Query title", "authors": "Query author"},
                      data={"auto_extract_metadata": "true", "title": "Manual", "authors": "Editor", "description": "Custom"})
    assert response.status_code == 201
    assert [response.json()[field] for field in ("title", "authors", "description")] == ["Manual", "Editor", "Custom"]


def test_legacy_upload_and_required_title(api):
    response = upload(api, params={"title": "Legacy", "authors": "Editor"})
    assert response.status_code == 201
    assert response.json()["title"] == "Legacy"
    assert response.json()["description"] is None
    assert response.json()["cover_url"] is None
    assert upload(api).status_code == 422
    assert upload(api, data={"auto_extract_metadata": "invalid"}).status_code == 422


@pytest.mark.parametrize("filename,raw,expected", [
    ("book.docx", docx_bytes(), "A book"),
    ("my_notes.txt", b"Notes", "my notes"),
    ("broken.pdf", b"broken", "broken"),
])
def test_other_formats_and_fallback(api, filename, raw, expected):
    response = upload(api, filename, raw, data={"auto_extract_metadata": "true"})
    assert response.status_code == 201
    assert response.json()["title"] == expected
    assert response.json()["cover_url"] is None
    client, headers, world_id = api
    assert client.get(f"/shreckworlds/{world_id}/library-items/{response.json()['id']}/cover", headers=headers).status_code == 404


def test_duplicate_titles_do_not_leave_files_and_worlds_are_independent(api, tmp_path):
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 201
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 409
    assert len(list((tmp_path / "media").rglob("cover.png"))) == 1
    client, headers, _ = api
    other = client.post("/admin/shreckworlds", headers=headers, json={"name": "Other", "shrecknet_world_id": "other"}).json()
    assert upload((client, headers, other["id"]), data={"auto_extract_metadata": "true"}).status_code == 201


def test_delete_removes_cover(api):
    client, headers, world_id = api
    item = upload(api, data={"auto_extract_metadata": "true"}).json()
    path = Path(item["cover_path"])
    assert path.is_file()
    assert client.delete(f"/admin/shreckworlds/{world_id}/library-items/{item['id']}", headers=headers).status_code == 204
    assert not path.exists()


def test_upload_role_and_size_checks_happen_before_extraction(api, monkeypatch):
    from app.main import app
    from app.api.deps import ShrecknetPrincipal, get_current_user, require_world_manager
    app.dependency_overrides.pop(require_world_manager)
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="2", role="player")
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="3", role="world_builder")
    monkeypatch.setenv("SHRECKWORLD_MAX_DOCUMENT_BYTES", "1")
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 413
    monkeypatch.setenv("SHRECKWORLD_MAX_DOCUMENT_BYTES", "100000")
    assert upload(api, "book.exe", b"bad", data={"auto_extract_metadata": "true"}).status_code == 415
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 201


def test_openapi_exposes_form_option_and_cover_url(api):
    client, _, _ = api
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/admin/shreckworlds/{shreckworld_id}/library-items"]["post"]
    body_ref = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"].split("/")[-1]
    properties = schema["components"]["schemas"][body_ref]["properties"]
    assert properties["auto_extract_metadata"]["default"] is False
    assert "title" in properties
    assert "cover_url" in schema["components"]["schemas"]["ShreckWorldLibraryItemRead"]["properties"]


def test_storage_failure_rolls_back_item_and_source(api, monkeypatch, tmp_path):
    original = Path.write_bytes
    def fail_cover(path, data):
        if path.name == "cover.png":
            raise OSError("storage unavailable")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_cover)
    assert upload(api, data={"auto_extract_metadata": "true"}).status_code == 500
    client, headers, world_id = api
    assert client.get(f"/admin/shreckworlds/{world_id}/library-items", headers=headers).json() == []
    assert not list((tmp_path / "media").rglob("source.pdf"))


def test_manual_limits_and_empty_file(api):
    assert upload(api, data={"title": "t" * 256}).status_code == 422
    assert upload(api, data={"title": "Title", "authors": "a" * 513}).status_code == 422
    assert upload(api, raw=b"", data={"auto_extract_metadata": "true"}).status_code == 413
