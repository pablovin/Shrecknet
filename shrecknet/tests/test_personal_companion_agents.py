from __future__ import annotations

from sqlalchemy import select

import pytest

from app.core.security import create_access_token
from app.models import Agent, BackgroundJob, JobStatus, Ontology
from app.models import User, UserRole


async def _create_user(session_maker, role: UserRole, suffix: str) -> tuple[User, dict[str, str]]:
    async with session_maker() as session:
        user = User(
            username=f"{role.value}-{suffix}",
            hashed_password="hashed",
            password="",
            full_name=f"{role.value.title()} User",
            email=f"{role.value}-{suffix}@example.com",
            timezone="UTC",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), role.value)
    return user, {"Authorization": f"Bearer {token}"}


async def _seed_ontology_with_tools(session_maker, *, ontology_id: int, suffix: str) -> None:
    async with session_maker() as session:
        ontology = Ontology(id=ontology_id, name=f"World-{suffix}", description="Test world")
        elder = Agent(
            id=f"elder-{suffix}",
            name=f"elder-{suffix}",
            job="elder",
            active=True,
        )
        librarian = Agent(
            id=f"librarian-{suffix}",
            name=f"librarian-{suffix}",
            job="librarian",
            active=True,
        )
        elder.ontologies.append(ontology)
        librarian.ontologies.append(ontology)
        session.add_all([ontology, elder, librarian])
        await session.commit()


@pytest.mark.asyncio
async def test_personal_companion_lifecycle(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-life")

    create_response = await client.post(
        "/users/me/companion",
        headers=headers,
        json={
            "name": "Echo",
            "writing_style": "Warm and concise",
            "active": True,
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["name"] == "Echo"

    get_response = await client.get("/users/me/companion", headers=headers)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["id"] == created["id"]

    patch_response = await client.patch(
        "/users/me/companion",
        headers=headers,
        json={"writing_style": "Reflective and playful", "active": False},
    )
    assert patch_response.status_code == 200, patch_response.text
    patched = patch_response.json()
    assert patched["writing_style"] == "Reflective and playful"
    assert patched["active"] is False

    delete_response = await client.delete("/users/me/companion", headers=headers)
    assert delete_response.status_code == 204, delete_response.text

    missing_response = await client.get("/users/me/companion", headers=headers)
    assert missing_response.status_code == 404, missing_response.text


@pytest.mark.asyncio
async def test_personal_companion_duplicate_create_returns_conflict(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-dup")

    first = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Echo", "writing_style": "Calm and clear"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Echo 2", "writing_style": "Different style"},
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_personal_companion_is_user_scoped(client, session_maker) -> None:
    _, headers_a = await _create_user(session_maker, UserRole.PLAYER, "companion-a")
    _, headers_b = await _create_user(session_maker, UserRole.PLAYER, "companion-b")

    create_response = await client.post(
        "/users/me/companion",
        headers=headers_a,
        json={"name": "A", "writing_style": "A style"},
    )
    assert create_response.status_code == 201, create_response.text

    get_a = await client.get("/users/me/companion", headers=headers_a)
    assert get_a.status_code == 200, get_a.text
    assert get_a.json()["name"] == "A"

    get_b = await client.get("/users/me/companion", headers=headers_b)
    assert get_b.status_code == 404, get_b.text


@pytest.mark.asyncio
async def test_personal_companion_avatar_upload_validation(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-avatar")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Sketch", "writing_style": "Friendly"},
    )
    assert created.status_code == 201, created.text

    invalid_upload = await client.post(
        "/users/me/companion/avatar",
        headers=headers,
        files={"file": ("avatar.txt", b"not-an-image", "text/plain")},
    )
    assert invalid_upload.status_code == 400, invalid_upload.text


@pytest.mark.asyncio
async def test_personal_companion_missing_returns_404_for_update_delete_avatar(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-missing")

    patch_response = await client.patch(
        "/users/me/companion",
        headers=headers,
        json={"name": "Any"},
    )
    assert patch_response.status_code == 404, patch_response.text

    delete_response = await client.delete("/users/me/companion", headers=headers)
    assert delete_response.status_code == 404, delete_response.text

    avatar_response = await client.post(
        "/users/me/companion/avatar",
        headers=headers,
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert avatar_response.status_code == 404, avatar_response.text


@pytest.mark.asyncio
async def test_orchestrator_bootstrap_allocates_world_tools(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "orch-bootstrap")
    await _seed_ontology_with_tools(session_maker, ontology_id=101, suffix="orch-bootstrap")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Guide", "writing_style": "Grounded and concise"},
    )
    assert created.status_code == 201, created.text

    bootstrap = await client.post(
        "/users/me/companion/orchestrator/bootstrap",
        headers=headers,
        json={"ontology_id": 101},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    payload = bootstrap.json()
    assert payload["ontology_id"] == 101
    assert payload["allocated_tools"]["elder"]
    assert payload["allocated_tools"]["librarian"]


@pytest.mark.asyncio
async def test_orchestrator_bootstrap_reuses_session_and_updates_world(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "orch-upsert")
    await _seed_ontology_with_tools(session_maker, ontology_id=201, suffix="orch-upsert-a")
    await _seed_ontology_with_tools(session_maker, ontology_id=202, suffix="orch-upsert-b")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Guide", "writing_style": "Grounded and concise"},
    )
    assert created.status_code == 201, created.text

    first_bootstrap = await client.post(
        "/users/me/companion/orchestrator/bootstrap",
        headers=headers,
        json={"ontology_id": 201},
    )
    assert first_bootstrap.status_code == 201, first_bootstrap.text
    first_payload = first_bootstrap.json()

    second_bootstrap = await client.post(
        "/users/me/companion/orchestrator/bootstrap",
        headers=headers,
        json={"ontology_id": 202},
    )
    assert second_bootstrap.status_code == 201, second_bootstrap.text
    second_payload = second_bootstrap.json()

    assert second_payload["session_id"] == first_payload["session_id"]
    assert second_payload["ontology_id"] == 202
    assert second_payload["allocated_tools"]["elder"]
    assert second_payload["allocated_tools"]["librarian"]


@pytest.mark.asyncio
async def test_orchestrator_queue_and_poll_lifecycle(client, session_maker, monkeypatch) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "orch-lifecycle")
    await _seed_ontology_with_tools(session_maker, ontology_id=102, suffix="orch-lifecycle")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Guide", "writing_style": "Grounded and concise"},
    )
    assert created.status_code == 201, created.text

    bootstrap = await client.post(
        "/users/me/companion/orchestrator/bootstrap",
        headers=headers,
        json={"ontology_id": 102},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    session_id = bootstrap.json()["session_id"]

    from app.tasks.companion_orchestrator import run_companion_orchestrator_turn

    monkeypatch.setattr(run_companion_orchestrator_turn, "delay", lambda **kwargs: None)

    queued = await client.post(
        f"/users/me/companion/orchestrator/chats/{session_id}/turns",
        headers=headers,
        json={"query": "Did Tamura reveal her pregnancy and what is the dexterity impact?"},
    )
    assert queued.status_code == 202, queued.text
    job_id = int(queued.json()["job_id"])

    queued_poll = await client.get(
        f"/users/me/companion/orchestrator/turns/{job_id}",
        headers=headers,
    )
    assert queued_poll.status_code == 200, queued_poll.text
    assert queued_poll.json()["status"] == "queued"

    chat_file = await client.get(
        f"/users/me/companion/orchestrator/chats/{session_id}/file",
        headers=headers,
    )
    assert chat_file.status_code == 200, chat_file.text
    chat_payload = chat_file.json()
    assert chat_payload["session_id"] == session_id
    assert chat_payload["messages"]
    assert chat_payload["messages"][0]["role"] == "user"
    assert "dexterity impact" in chat_payload["messages"][0]["content"].lower()

    async with session_maker() as session:
        result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
        job = result.scalar_one()
        job.status = JobStatus.DONE
        job.details = (
            '{"status":"done","final":{"text":"answer","annotated_text":"answer\\n\\nClaim Anchors:\\n- [C1] claim (sources: S1)"},"claims":[{"claim_id":"C1","text":"claim","source_ids":["S1"]}]}'
        )
        await session.commit()

    done_poll = await client.get(
        f"/users/me/companion/orchestrator/turns/{job_id}",
        headers=headers,
    )
    assert done_poll.status_code == 200, done_poll.text
    done_payload = done_poll.json()
    assert done_payload["status"] == "done"
    assert "Claim Anchors" in done_payload["payload"]["final"]["annotated_text"]


@pytest.mark.asyncio
async def test_orchestrator_queue_refreshes_allocated_tools(client, session_maker, monkeypatch) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "orch-refresh-tools")
    await _seed_ontology_with_tools(session_maker, ontology_id=301, suffix="orch-refresh-a")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Guide", "writing_style": "Grounded and concise"},
    )
    assert created.status_code == 201, created.text

    bootstrap = await client.post(
        "/users/me/companion/orchestrator/bootstrap",
        headers=headers,
        json={"ontology_id": 301},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    session_id = bootstrap.json()["session_id"]

    # Add more tools after bootstrap; queue should refresh and persist these.
    await _seed_ontology_with_tools(session_maker, ontology_id=301, suffix="orch-refresh-b")

    from app.tasks.companion_orchestrator import run_companion_orchestrator_turn

    monkeypatch.setattr(run_companion_orchestrator_turn, "delay", lambda **kwargs: None)

    queued = await client.post(
        f"/users/me/companion/orchestrator/chats/{session_id}/turns",
        headers=headers,
        json={"query": "What rules apply?"},
    )
    assert queued.status_code == 202, queued.text
    job_id = int(queued.json()["job_id"])

    async with session_maker() as session:
        result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
        job = result.scalar_one()
        details = job.details or {}
        if isinstance(details, str):
            import json

            details = json.loads(details)

    allocated_tools = details.get("allocated_tools") or {}
    assert len(allocated_tools.get("elder") or []) >= 2
    assert len(allocated_tools.get("librarian") or []) >= 2
