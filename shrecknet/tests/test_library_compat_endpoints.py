from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import pytest

from app.core.security import create_access_token
from app.models import AuthorType, BackgroundJob, JobStatus, JobType, LibraryItem, User, UserRole

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


class _FakeResult:
    def __init__(
        self,
        record: dict[str, object] | None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._record = record
        self._rows = rows or []

    async def single(self) -> dict[str, object] | None:
        return self._record

    async def data(self) -> list[dict[str, object]]:
        return self._rows


class _FakeGraphSession:
    def __init__(
        self,
        record: dict[str, object] | None,
        calls: list[dict[str, object]] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._record = record
        self._calls = calls
        self._rows = rows

    async def run(self, *_args, **_kwargs) -> _FakeResult:
        if self._calls is not None:
            self._calls.append(dict(_kwargs))
        return _FakeResult(self._record, self._rows)


class _FakeSessionContext:
    def __init__(
        self,
        record: dict[str, object] | None,
        calls: list[dict[str, object]] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._session = _FakeGraphSession(record, calls, rows)

    async def __aenter__(self) -> _FakeGraphSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeDriver:
    def __init__(
        self,
        record: dict[str, object] | None,
        calls: list[dict[str, object]] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._record = record
        self._calls = calls
        self._rows = rows

    def session(self, **_kwargs) -> _FakeSessionContext:
        return _FakeSessionContext(self._record, self._calls, self._rows)


async def _create_user(session_maker, role: UserRole) -> tuple[User, dict[str, str]]:
    async with session_maker() as session:
        user = User(
            username=f"{role.value}-user",
            hashed_password="hashed",
            password="",
            full_name=f"{role.value.title()} User",
            email=f"{role.value}@example.com",
            timezone="UTC",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), role.value)
    return user, {"Authorization": f"Bearer {token}"}


async def _create_library_item(client, headers: dict[str, str], ontology_id: str = "1") -> dict:
    response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=headers,
        files={"file": ("content.pdf", BytesIO(PDF_BYTES), "application/pdf")},
        data={"title": "Compat Book"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch_jobs_sessionmaker(monkeypatch, session_maker) -> None:
    monkeypatch.setattr(
        "app.api.routers.libraries.JobsSessionMaker",
        session_maker,
    )


@pytest.mark.asyncio
async def test_put_pdf_alias_replaces_library_content(client, session_maker) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    item = await _create_library_item(client, admin_headers)

    response = await client.put(
        f"/libraries/1/items/{item['id']}/pdf",
        headers=admin_headers,
        files={"file": ("replacement.pdf", BytesIO(PDF_BYTES + b"\n"), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == item["id"]
    assert response.json()["pdf_path"].endswith(f"/{item['id']}/content.pdf")


@pytest.mark.asyncio
async def test_legacy_embedding_stats_endpoint(client, session_maker, monkeypatch) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    _patch_jobs_sessionmaker(monkeypatch, session_maker)
    item_1 = await _create_library_item(client, admin_headers)
    item_2 = await _create_library_item(client, admin_headers)
    item_3 = await _create_library_item(client, admin_headers)

    async def _fake_chunk_counts(_ontology_id: int) -> dict[int, int]:
        return {item_1["id"]: 4}

    monkeypatch.setattr("app.api.routers.libraries._get_pdf_chunk_counts", _fake_chunk_counts)

    async with session_maker() as session:
        db_item_1 = await session.get(LibraryItem, item_1["id"])
        assert db_item_1 is not None
        db_item_1.vectorized = True
        session.add(
            BackgroundJob(
                celery_task_id="failed-job",
                author_type=AuthorType.USER,
                author_id="1",
                kind=JobType.PDF_BOOK_EMBEDDING.value,
                job_type=JobType.PDF_BOOK_EMBEDDING,
                status=JobStatus.FAILED,
                description=f"Embedding PDF book (library item {item_2['id']})",
                details=f'{{"library_item_id": {item_2["id"]}}}',
                progress=1.0,
                ontology_id=1,
            )
        )
        await session.commit()

    response = await client.get(
        "/libraries/embedding-stats",
        headers=admin_headers,
        params={"ontology_id": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ontology_id": 1,
        "total_nodes": 3,
        "embedded_nodes": 1,
        "unembedded_nodes": 2,
        "outdated_nodes": 1,
        "processing_nodes": 0,
        "failed_nodes": 1,
    }


@pytest.mark.asyncio
async def test_legacy_embedding_stats_counts_chunks_without_chunk_ontology(
    client, session_maker, monkeypatch
) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    _patch_jobs_sessionmaker(monkeypatch, session_maker)
    item = await _create_library_item(client, admin_headers)

    async with session_maker() as session:
        db_item = await session.get(LibraryItem, item["id"])
        assert db_item is not None
        db_item.vectorized = True
        await session.commit()

    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver(
            None,
            rows=[
                {
                    "library_item_id": item["id"],
                    "chunk_count": 2,
                }
            ],
        ),
    )

    response = await client.get(
        "/libraries/embedding-stats",
        headers=admin_headers,
        params={"ontology_id": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json()["embedded_nodes"] == 1
    assert response.json()["unembedded_nodes"] == 0


@pytest.mark.asyncio
async def test_legacy_embedding_jobs_endpoint(client, session_maker, monkeypatch) -> None:
    user, headers = await _create_user(session_maker, UserRole.ADMIN)
    _patch_jobs_sessionmaker(monkeypatch, session_maker)
    started_at = datetime.now(timezone.utc)

    async with session_maker() as session:
        job = BackgroundJob(
            celery_task_id="compat-job",
            author_type=AuthorType.USER,
            author_id=str(user.id),
            kind=JobType.PDF_BOOK_EMBEDDING.value,
            job_type=JobType.PDF_BOOK_EMBEDDING,
            status=JobStatus.RUNNING,
            description="Embedding PDF book (library item 1)",
            details='{"library_item_id": 1}',
            progress=0.4,
            ontology_id=1,
            started_at=started_at,
        )
        session.add(job)
        await session.commit()

    response = await client.get(
        "/libraries/embedding-jobs",
        headers=headers,
        params={"ontology_id": 1},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert data[0]["library_item_id"] == 1
    assert data[0]["ontology_id"] == 1
    assert data[0]["status"] == JobStatus.RUNNING.value
    assert data[0]["description"] == "Embedding PDF book (library item 1)"
    assert data[0]["details"] == {"library_item_id": 1}


@pytest.mark.asyncio
async def test_embedding_status_uses_latest_job_for_requested_item(
    client, session_maker, monkeypatch
) -> None:
    user, headers = await _create_user(session_maker, UserRole.ADMIN)
    _patch_jobs_sessionmaker(monkeypatch, session_maker)
    item_1 = await _create_library_item(client, headers)
    item_2 = await _create_library_item(client, headers)

    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver({"total_chunks": 0}),
    )

    async with session_maker() as session:
        session.add_all(
            [
                BackgroundJob(
                    celery_task_id="older-item-job",
                    author_type=AuthorType.USER,
                    author_id=str(user.id),
                    kind=JobType.PDF_BOOK_EMBEDDING.value,
                    job_type=JobType.PDF_BOOK_EMBEDDING,
                    status=JobStatus.FAILED,
                    description=f"Embedding PDF book (library item {item_1['id']})",
                    details=f'{{"library_item_id": {item_1["id"]}, "status": "failed"}}',
                    progress=1.0,
                    ontology_id=1,
                ),
                BackgroundJob(
                    celery_task_id="newer-other-item-job",
                    author_type=AuthorType.USER,
                    author_id=str(user.id),
                    kind=JobType.PDF_BOOK_EMBEDDING.value,
                    job_type=JobType.PDF_BOOK_EMBEDDING,
                    status=JobStatus.RUNNING,
                    description=f"Embedding PDF book (library item {item_2['id']})",
                    details=f'{{"library_item_id": {item_2["id"]}, "status": "running"}}',
                    progress=0.5,
                    ontology_id=1,
                ),
            ]
        )
        await session.commit()

    response = await client.get(
        f"/libraries/1/items/{item_1['id']}/embedding-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["library_item_id"] == item_1["id"]
    assert data["job_status"] == JobStatus.FAILED.value
    assert data["job_details"] == {"library_item_id": item_1["id"], "status": "failed"}


@pytest.mark.asyncio
async def test_legacy_clear_item_embeddings_endpoint(client, session_maker, monkeypatch) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    item = await _create_library_item(client, admin_headers)
    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver({"total": 7}),
    )

    response = await client.delete(
        f"/libraries/1/items/{item['id']}/embeddings",
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["library_item_id"] == item["id"]
    assert response.json()["chunks_deleted"] == 7


@pytest.mark.asyncio
async def test_delete_library_item_clears_neo4j_embeddings(client, session_maker, monkeypatch) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    item = await _create_library_item(client, admin_headers)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver({"total": 2}, calls),
    )

    response = await client.delete(
        f"/libraries/1/items/{item['id']}",
        headers=admin_headers,
    )

    assert response.status_code == 204, response.text
    assert calls == [{"library_item_id": item["id"]}]


@pytest.mark.asyncio
async def test_bulk_delete_library_items_clears_neo4j_embeddings(
    client, session_maker, monkeypatch
) -> None:
    _, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    item_1 = await _create_library_item(client, admin_headers)
    item_2 = await _create_library_item(client, admin_headers)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver({"total": 1}, calls),
    )

    response = await client.post(
        "/libraries/1/items/bulk-delete",
        headers=admin_headers,
        json={"item_ids": [item_1["id"], item_2["id"]]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 2
    assert calls == [
        {"library_item_id": item_1["id"]},
        {"library_item_id": item_2["id"]},
    ]


@pytest.mark.asyncio
async def test_legacy_clear_all_embeddings_endpoint(client, session_maker, monkeypatch) -> None:
    user, admin_headers = await _create_user(session_maker, UserRole.ADMIN)
    _patch_jobs_sessionmaker(monkeypatch, session_maker)
    await _create_library_item(client, admin_headers, ontology_id="1")
    await _create_library_item(client, admin_headers, ontology_id="1")
    monkeypatch.setattr(
        "app.api.routers.libraries.get_driver",
        lambda: _FakeDriver({"total": 3}),
    )

    async with session_maker() as session:
        session.add(
            BackgroundJob(
                celery_task_id="queued-job",
                author_type=AuthorType.USER,
                author_id=str(user.id),
                kind=JobType.PDF_BOOK_EMBEDDING.value,
                job_type=JobType.PDF_BOOK_EMBEDDING,
                status=JobStatus.QUEUED,
                description="Queued embedding",
                progress=0.0,
                ontology_id=1,
            )
        )
        await session.commit()

    response = await client.delete(
        "/libraries/admin/clear-all-embeddings",
        headers=admin_headers,
        params={"ontology_id": "1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["items_affected"] == 2
    assert response.json()["chunks_deleted"] == 6
    assert response.json()["jobs_deleted"] == 1
