from __future__ import annotations

from typing import Any

import httpx
import logging

from app.core.config import Settings
from app.schemas import AllocatedToolAgent, OrchestratorToolAllocation

logger = logging.getLogger(__name__)


class ShreckLLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.shreckllm_base_url.rstrip("/"),
            timeout=settings.provider_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def chat(
        self,
        *,
        provider_id: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        usage_tag: str,
    ) -> str:
        response = await self.client.post(
            "/chat",
            json={
                "provider_id": provider_id,
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "metadata": {"usage_tag": usage_tag},
            },
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("text") or data.get("content") or data.get("message") or "")


class ShrecknetProviderClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.shrecknet_api_base_url.rstrip("/"),
            timeout=settings.provider_timeout_seconds,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    def _headers(self, user_id: int, auth_header: str | None = None) -> dict[str, str]:
        headers = {"X-Shreck-User-Id": str(user_id)}
        if auth_header:
            headers["Authorization"] = auth_header
            return headers
        if self.settings.internal_service_token:
            headers["Authorization"] = f"Bearer {self.settings.internal_service_token}"
            headers["X-Internal-Service-Token"] = self.settings.internal_service_token
        return headers

    async def allocate_tools(
        self,
        *,
        user_id: int,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> OrchestratorToolAllocation:
        try:
            logger.info("provider allocation started user_id=%s ontology_id=%s", user_id, ontology_id)
            elder_response = await self.client.get(
                "/agents/",
                params={"job": "elder", "active": "true"},
                headers=self._headers(user_id, auth_header),
            )
            elder_response.raise_for_status()
            librarian_response = await self.client.get(
                "/agents/",
                params={"job": "librarian", "active": "true"},
                headers=self._headers(user_id, auth_header),
            )
            librarian_response.raise_for_status()
            allocation = self._parse_allocation(
                {
                    "elder": elder_response.json(),
                    "librarian": librarian_response.json(),
                },
                ontology_id=ontology_id,
            )
            logger.info(
                "provider allocation completed user_id=%s ontology_id=%s elder=%s librarian=%s",
                user_id,
                ontology_id,
                len(allocation.elder),
                len(allocation.librarian),
            )
            return allocation
        except httpx.HTTPStatusError:
            raise
        except Exception:
            pass
        raise RuntimeError("Could not allocate Elder/Librarian tools from Shrecknet")

    def _parse_allocation(self, data: Any, *, ontology_id: int | None = None) -> OrchestratorToolAllocation:
        if isinstance(data, dict) and ("elder" in data or "librarian" in data):
            if all(isinstance(data.get(key), list) for key in ("elder", "librarian") if key in data):
                elder = self._parse_agent_rows(data.get("elder") or [], expected_job="elder", ontology_id=ontology_id)
                librarian = self._parse_agent_rows(
                    data.get("librarian") or [],
                    expected_job="librarian",
                    ontology_id=ontology_id,
                )
                allocation = OrchestratorToolAllocation(elder=elder, librarian=librarian)
                if allocation.elder or allocation.librarian:
                    return allocation
            return OrchestratorToolAllocation.model_validate(data)
        rows = data.get("items") if isinstance(data, dict) else data
        allocation = OrchestratorToolAllocation(
            elder=self._parse_agent_rows(rows or [], expected_job="elder", ontology_id=ontology_id),
            librarian=self._parse_agent_rows(rows or [], expected_job="librarian", ontology_id=ontology_id),
        )
        if not allocation.elder and not allocation.librarian:
            raise RuntimeError("No active Elder/Librarian tools returned by Shrecknet")
        return allocation

    def _parse_agent_rows(
        self,
        rows: Any,
        *,
        expected_job: str | None = None,
        ontology_id: int | None = None,
    ) -> list[AllocatedToolAgent]:
        parsed: list[AllocatedToolAgent] = []
        if not isinstance(rows, list):
            return parsed
        for row in rows:
            if not isinstance(row, dict):
                continue
            job = str(row.get("job") or expected_job or "")
            if expected_job and job != expected_job:
                continue
            ontology_ids = [int(x) for x in row.get("ontology_ids", []) if str(x).isdigit()]
            if ontology_id is not None and ontology_ids and ontology_id not in ontology_ids:
                continue
            parsed.append(
                AllocatedToolAgent(
                    id=str(row.get("id")),
                    name=str(row.get("name") or row.get("id")),
                    job=job,
                    ontology_ids=ontology_ids,
                )
            )
        return parsed

    async def run_elder(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        return await self._run_provider(
            user_id=user_id,
            path=f"/jobs/elder/{agent_id}/query",
            payload={"query": query, "mode": "both"},
            job="elder",
            agent_id=agent_id,
            auth_header=auth_header,
        )

    async def run_librarian(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        return await self._run_provider(
            user_id=user_id,
            path=f"/jobs/librarian/{agent_id}/query",
            payload={"query": query, "mode": "both"},
            job="librarian",
            agent_id=agent_id,
            auth_header=auth_header,
        )

    async def _run_provider(
        self,
        *,
        user_id: int,
        path: str,
        payload: dict[str, Any],
        job: str,
        agent_id: str,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        try:
            logger.info("provider query started job=%s agent_id=%s path=%s", job, agent_id, path)
            response = await self.client.post(path, json=payload, headers=self._headers(user_id, auth_header))
            response.raise_for_status()
            data = response.json()
            logger.info("provider query completed job=%s agent_id=%s path=%s", job, agent_id, path)
            return data if isinstance(data, dict) else {"answer": str(data)}
        except Exception as exc:
            last_error = str(exc)
            logger.warning("provider query failed job=%s agent_id=%s path=%s error=%s", job, agent_id, path, last_error)
        return {
            "ok": False,
            "agent_id": agent_id,
            "agent_name": agent_id,
            "agent_job": job,
            "answer": "",
            "sources": [],
            "error": last_error,
        }
