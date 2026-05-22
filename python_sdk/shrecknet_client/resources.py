from __future__ import annotations

from typing import Any, Awaitable, Callable

import asyncio
import json
import random
import time
import httpx

from .client import AsyncShrecknetClient
from .errors import ArchitectPreflightError, ConfigurationReadinessError, ElderPreflightError, JobFailedError, JobTimeoutError
from .models import (
    BackgroundJobRecord,
    ArchitectAnalysisRequest,
    ArchitectGenerationRequest,
    ArchitectPreflightReport,
    ArchitectProposalCreate,
    ArchitectProposalRead,
    ArchitectProposalStatusUpdate,
    ArchitectProposalUpdate,
    ArchitectRunRead,
    ArchitectRunSummary,
    EmbeddingStats,
    EmbeddingTriggerResponse,
    EmbeddingLifecycleReport,
    ElderChatCreate,
    ElderChatRead,
    ElderChatsList,
    ElderChatUpdate,
    ElderChatWithHistory,
    ElderPreflightReport,
    ElderQueryRequest,
    ElderQueryResponse,
    GraphRAGEmbedNodeResult,
    GraphRAGEmbedOntologyResult,
    GraphRAGIndexStatus,
    GraphRAGResetEmbeddingsResult,
    AgentCreate,
    AgentRead,
    AgentUpdate,
    LLMReadinessReport,
    Ontology,
    OntologyEntityResolveResponse,
    OntologyInstance,
    OntologyInstanceCount,
    OntologyInstanceCreate,
    OntologyInstanceSceneCountsResponse,
    OntologyInstanceSearchResponse,
    OntologyInstanceSummaryPage,
    OntologyInstanceUpdate,
    OntologyWorldStatsResponse,
    ProviderStatus,
    ProviderValidation,
    World,
)


class WorldsAPI:
    """World read endpoints."""

    def __init__(self, client: AsyncShrecknetClient):
        self._client = client

    async def list(self) -> list[World]:
        """List worlds visible to current user."""
        data = await self._client.raw_request("GET", "/worlds")
        return [World.model_validate(item) for item in data]

    async def get(self, world_id: str) -> World:
        """Fetch a world by id."""
        data = await self._client.raw_request("GET", f"/worlds/{world_id}")
        return World.model_validate(data)


class OntologiesAPI:
    """Ontology CRUD and world stats endpoints."""

    def __init__(self, client: AsyncShrecknetClient):
        self._client = client

    async def create(self, *, name: str, description: str | None = None, image_url: str | None = None) -> Ontology:
        """Create ontology."""
        data = await self._client.raw_request(
            "POST", "/ontologies/", json={"name": name, "description": description, "image_url": image_url}
        )
        return Ontology.model_validate(data)

    async def list(self, *, name: str | None = None, description: str | None = None, skip: int = 0, limit: int = 50) -> list[Ontology]:
        """List ontologies with optional filters and pagination."""
        params = {"skip": skip, "limit": limit}
        if name is not None:
            params["name"] = name
        if description is not None:
            params["description"] = description
        data = await self._client.raw_request("GET", "/ontologies/", params=params)
        return [Ontology.model_validate(item) for item in data]

    async def get(self, ontology_id: int) -> Ontology:
        """Fetch ontology by id."""
        data = await self._client.raw_request("GET", f"/ontologies/{ontology_id}")
        return Ontology.model_validate(data)

    async def update(self, ontology_id: int, **fields: Any) -> Ontology:
        """Patch ontology fields."""
        data = await self._client.raw_request("PUT", f"/ontologies/{ontology_id}", json=fields)
        return Ontology.model_validate(data)

    async def delete(self, ontology_id: int) -> None:
        """Delete ontology by id."""
        await self._client.raw_request("DELETE", f"/ontologies/{ontology_id}")

    async def world_stats(self, *, ontology_ids: list[int] | None = None, include_content_counts: bool = True) -> OntologyWorldStatsResponse:
        """Return world stats for one or multiple ontologies."""
        params: dict[str, Any] = {"include_content_counts": include_content_counts}
        if ontology_ids is not None:
            params["ontology_ids"] = ",".join(str(x) for x in ontology_ids)
        data = await self._client.raw_request("GET", "/ontologies/world-stats", params=params)
        return OntologyWorldStatsResponse.model_validate(data)


class OntologyInstancesAPI:
    """Ontology instance CRUD, search, and summary endpoints."""

    def __init__(self, client: AsyncShrecknetClient):
        self._client = client

    async def create(self, payload: OntologyInstanceCreate) -> OntologyInstance:
        """Create ontology instance."""
        data = await self._client.raw_request("POST", "/ontology-instances/", json=payload.model_dump(exclude_none=True))
        return OntologyInstance.model_validate(data)

    async def list(self, *, skip: int = 0, limit: int = 50, search: str | None = None, ontology_id: int | None = None) -> list[OntologyInstance]:
        """List ontology instances with optional filters."""
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if search:
            params["search"] = search
        if ontology_id is not None:
            params["ontology_id"] = ontology_id
        data = await self._client.raw_request("GET", "/ontology-instances/", params=params)
        return [OntologyInstance.model_validate(item) for item in data]

    async def get(self, instance_id: str) -> OntologyInstance:
        """Fetch ontology instance by id."""
        data = await self._client.raw_request("GET", f"/ontology-instances/{instance_id}")
        return OntologyInstance.model_validate(data)

    async def update(self, instance_id: str, payload: OntologyInstanceUpdate) -> OntologyInstance:
        """Update ontology instance."""
        data = await self._client.raw_request(
            "PUT", f"/ontology-instances/{instance_id}", json=payload.model_dump(exclude_none=True)
        )
        return OntologyInstance.model_validate(data)

    async def delete(self, instance_id: str) -> None:
        """Delete ontology instance."""
        await self._client.raw_request("DELETE", f"/ontology-instances/{instance_id}")

    async def count(self, *, ontology_id: int | None = None, entity_definition_id: int | None = None, search: str | None = None) -> OntologyInstanceCount:
        """Count ontology instances for filters."""
        params: dict[str, Any] = {}
        if ontology_id is not None:
            params["ontology_id"] = ontology_id
        if entity_definition_id is not None:
            params["entity_definition_id"] = entity_definition_id
        if search is not None:
            params["search"] = search
        data = await self._client.raw_request("GET", "/ontology-instances/count", params=params)
        return OntologyInstanceCount.model_validate(data)

    async def search(self, *, query: str, ontology_id: int, limit: int = 20) -> OntologyInstanceSearchResponse:
        """Search instances by query within ontology scope."""
        data = await self._client.raw_request(
            "GET", "/ontology-instances/search", params={"query": query, "ontology_id": ontology_id, "limit": limit}
        )
        return OntologyInstanceSearchResponse.model_validate(data)

    async def basic(self, *, skip: int = 0, limit: int = 50, ontology_id: int | None = None, entity_definition_id: int | None = None, search: str | None = None) -> OntologyInstanceSummaryPage:
        """Return summary page for instance listing UIs."""
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if ontology_id is not None:
            params["ontology_id"] = ontology_id
        if entity_definition_id is not None:
            params["entity_definition_id"] = entity_definition_id
        if search is not None:
            params["search"] = search
        data = await self._client.raw_request("GET", "/ontology-instances/basic", params=params)
        return OntologyInstanceSummaryPage.model_validate(data)

    async def resolve_entities(self, *, ontology_id: int, entity_instance_ids: list[str]) -> OntologyEntityResolveResponse:
        """Resolve entity instance ids into scoped ontology entities."""
        data = await self._client.raw_request(
            "POST", "/ontology-instances/resolve-entities", json={"ontology_id": ontology_id, "entity_instance_ids": entity_instance_ids}
        )
        return OntologyEntityResolveResponse.model_validate(data)

    async def scene_counts(self, instance_ids: list[str]) -> OntologyInstanceSceneCountsResponse:
        """Return scene counts for the provided ontology instance ids."""
        data = await self._client.raw_request("POST", "/ontology-instances/scenes/counts", json={"instance_ids": instance_ids})
        return OntologyInstanceSceneCountsResponse.model_validate(data)


class AgentsAPI:
    """Agent management endpoints."""

    def __init__(self, client: AsyncShrecknetClient):
        self._client = client

    async def available_jobs(self) -> list[str]:
        return await self._client.raw_request("GET", "/agents/jobs")

    async def list(self, *, job: str | None = None, active: bool | None = None, limit: int = 100, offset: int = 0) -> list[AgentRead]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if job is not None:
            params["job"] = job
        if active is not None:
            params["active"] = active
        data = await self._client.raw_request("GET", "/agents/", params=params)
        return [AgentRead.model_validate(item) for item in data]

    async def create(self, payload: AgentCreate) -> AgentRead:
        data = await self._client.raw_request("POST", "/agents/", json=payload.model_dump(exclude_none=True))
        return AgentRead.model_validate(data)

    async def get(self, agent_id: str) -> AgentRead:
        data = await self._client.raw_request("GET", f"/agents/{agent_id}")
        return AgentRead.model_validate(data)

    async def update(self, agent_id: str, payload: AgentUpdate) -> AgentRead:
        data = await self._client.raw_request("PATCH", f"/agents/{agent_id}", json=payload.model_dump(exclude_none=True))
        return AgentRead.model_validate(data)

    async def delete(self, agent_id: str) -> None:
        await self._client.raw_request("DELETE", f"/agents/{agent_id}")

    async def attach_ontology(self, agent_id: str, ontology_id: int) -> AgentRead:
        data = await self._client.raw_request("POST", f"/agents/{agent_id}/ontologies/{ontology_id}")
        return AgentRead.model_validate(data)

    async def detach_ontology(self, agent_id: str, ontology_id: int) -> AgentRead:
        data = await self._client.raw_request("DELETE", f"/agents/{agent_id}/ontologies/{ontology_id}")
        return AgentRead.model_validate(data)


class ShreckLLMAPI:
    """shreckLLM configuration and status endpoints (plus Shrecknet llm_status)."""

    def __init__(self, client: AsyncShrecknetClient, base_url: str = "http://localhost:8111", timeout: float = 30.0):
        self._client = client
        self._llm = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        await self._llm.aclose()

    def _headers(self) -> dict[str, str]:
        if not self._client.token:
            return {}
        return {"Authorization": f"Bearer {self._client.token}"}

    async def _llm_request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        response = await self._llm.request(method, path, json=json, headers=self._headers())
        detail = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
        except Exception:
            detail = response.text
        from .errors import raise_for_status

        raise_for_status(response.status_code, detail)
        return response.json()

    async def llm_status(self) -> dict[str, Any]:
        return await self._client.raw_request("GET", "/llm_status/")

    async def health(self) -> dict[str, Any]:
        return await self._llm_request("GET", "/health")

    async def ready(self) -> dict[str, Any]:
        return await self._llm_request("GET", "/ready")

    async def models(self) -> dict[str, Any]:
        return await self._llm_request("GET", "/models")

    async def status(self) -> dict[str, Any]:
        return await self._llm_request("GET", "/status")

    async def get_config(self) -> dict[str, Any]:
        return await self._llm_request("GET", "/config")

    async def put_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._llm_request("PUT", "/config", json=payload)

    async def reload_config(self) -> dict[str, Any]:
        return await self._llm_request("POST", "/config/reload")

    async def set_openai_key(self, api_key: str) -> dict[str, Any]:
        return await self._llm_request("PUT", "/config/openai-token", json={"api_key": api_key})

    async def clear_openai_key(self) -> dict[str, Any]:
        return await self._llm_request("DELETE", "/config/openai-token")

    async def validate_openai(self) -> ProviderValidation:
        return ProviderValidation.model_validate(await self._llm_request("GET", "/providers/openai/validate"))

    async def set_anthropic_key(self, api_key: str) -> dict[str, Any]:
        return await self._llm_request("PUT", "/config/anthropic-token", json={"api_key": api_key})

    async def clear_anthropic_key(self) -> dict[str, Any]:
        return await self._llm_request("DELETE", "/config/anthropic-token")

    async def validate_anthropic(self) -> ProviderValidation:
        return ProviderValidation.model_validate(await self._llm_request("GET", "/providers/anthropic/validate"))

    async def add_provider_model(self, provider_id: str, model: str) -> dict[str, Any]:
        return await self._llm_request("POST", f"/config/providers/{provider_id}/models", json={"model": model})

    async def remove_provider_model(self, provider_id: str, model: str) -> dict[str, Any]:
        return await self._llm_request("DELETE", f"/config/providers/{provider_id}/models", json={"model": model})

    async def check_shreckllm_reachable(self) -> bool:
        try:
            payload = await self.llm_status()
        except Exception:
            return False
        return bool(payload.get("shreckllm", {}).get("reachable") is True)

    async def list_provider_statuses(self) -> list[ProviderStatus]:
        statuses: list[ProviderStatus] = []
        models_payload = await self.models()
        provider_models = models_payload.get("providers", {}) if isinstance(models_payload, dict) else {}

        for provider_id in ("openai", "anthropic"):
            validator = getattr(self, f"validate_{provider_id}")
            info = await validator()
            models = provider_models.get(provider_id, {}).get("models", []) if isinstance(provider_models, dict) else []
            statuses.append(
                ProviderStatus(
                    provider_id=provider_id,
                    enabled=bool(info.valid),
                    valid=info.valid,
                    configured=info.configured,
                    error=info.error,
                    models=[str(m) for m in models],
                )
            )
        return statuses

    async def has_any_provider_ready(self) -> bool:
        providers = await self.list_provider_statuses()
        return any((p.valid is True) and len(p.models) > 0 for p in providers)

    async def preflight_agents_llm_ready(self, *, strict: bool = False) -> LLMReadinessReport:
        reasons: list[str] = []
        reachable = await self.check_shreckllm_reachable()
        if not reachable:
            reasons.append("shreckLLM is not reachable from Shrecknet")

        providers: list[ProviderStatus] = []
        any_provider_ready = False
        if reachable:
            try:
                providers = await self.list_provider_statuses()
                any_provider_ready = any((p.valid is True) and len(p.models) > 0 for p in providers)
            except Exception as exc:
                reasons.append(f"Failed to fetch provider status: {exc}")
        if reachable and not any_provider_ready:
            reasons.append("No provider is both valid and has at least one model")

        report = LLMReadinessReport(
            checks={
                "shreckllm_reachable": reachable,
                "any_provider_ready": any_provider_ready,
            },
            providers=providers,
            ready=reachable and any_provider_ready,
            reasons=reasons,
        )
        if strict and not report.ready:
            raise ConfigurationReadinessError(reasons)
        return report


def _parse_job_details(details: Any) -> dict[str, Any] | str | None:
    if details is None:
        return None
    if isinstance(details, dict):
        return details
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return details
        return details
    return str(details)


def _normalize_job(payload: dict[str, Any]) -> BackgroundJobRecord:
    job_id_raw = payload.get("id") or payload.get("job_id")
    job_id = int(job_id_raw)
    job_type = str(payload.get("job_type") or payload.get("kind") or "unknown")
    status = str(payload.get("status") or "queued")
    return BackgroundJobRecord(
        id=job_id,
        job_type=job_type,
        status=status,
        author_type=payload.get("author_type"),
        author_id=payload.get("author_id"),
        ontology_id=payload.get("ontology_id"),
        description=str(payload.get("description") or ""),
        details=_parse_job_details(payload.get("details")),
        progress=float(payload.get("progress") or 0.0),
        error_message=payload.get("error_message"),
        started_at=payload.get("started_at"),
        completed_at=payload.get("completed_at"),
        duration_seconds=payload.get("duration_seconds"),
        updated_at=payload.get("updated_at"),
        raw=payload,
    )


class JobsAPI:
    """Generic background jobs API with async wait helpers."""

    def __init__(self, client: AsyncShrecknetClient):
        self._client = client

    async def list(self, **filters: Any) -> list[BackgroundJobRecord]:
        data = await self._client.raw_request("GET", "/jobs/", params=filters or None)
        return [_normalize_job(item) for item in data]

    async def get(self, job_id: int) -> BackgroundJobRecord:
        data = await self._client.raw_request("GET", f"/jobs/{job_id}")
        return _normalize_job(data)

    async def delete_many(self, jobs: list[BackgroundJobRecord]) -> int:
        payload = {"jobs": [{"kind": j.job_type, "job_id": str(j.id)} for j in jobs]}
        data = await self._client.raw_request("DELETE", "/jobs/", json=payload)
        return int(data.get("deleted_count", 0))

    async def delete_if_terminal(self, jobs: list[BackgroundJobRecord]) -> int:
        terminal = [j for j in jobs if j.is_terminal]
        if not terminal:
            return 0
        return await self.delete_many(terminal)

    async def wait(
        self,
        job_id: int,
        *,
        timeout_s: float = 300,
        poll_interval_s: float = 0.5,
        backoff: float = 1.3,
        max_interval_s: float = 5.0,
        jitter: float = 0.1,
        on_update: Callable[[BackgroundJobRecord | None, BackgroundJobRecord, float], Awaitable[None] | None] | None = None,
        strict: bool = True,
    ) -> BackgroundJobRecord:
        started = time.monotonic()
        interval = poll_interval_s
        prev: BackgroundJobRecord | None = None

        while True:
            current = await self.get(job_id)
            elapsed = time.monotonic() - started

            if on_update is not None:
                maybe = on_update(prev, current, elapsed)
                if asyncio.iscoroutine(maybe):
                    await maybe

            if current.is_terminal:
                if strict and current.failed:
                    raise JobFailedError(current.id, current.error_message, current.details)
                return current

            if elapsed >= timeout_s:
                raise JobTimeoutError(job_id, timeout_s)

            sleep_for = min(interval, max_interval_s)
            if jitter > 0:
                sleep_for = max(0.05, sleep_for + random.uniform(-jitter, jitter))
            await asyncio.sleep(sleep_for)
            interval = min(interval * backoff, max_interval_s)
            prev = current

    async def wait_many(self, job_ids: list[int], *, concurrency: int = 5, **wait_kwargs: Any) -> list[BackgroundJobRecord]:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(jid: int) -> BackgroundJobRecord:
            async with sem:
                return await self.wait(jid, **wait_kwargs)

        return await asyncio.gather(*[_one(j) for j in job_ids])


class JobHandle:
    """Typed handle around one background job."""

    def __init__(self, jobs_api: JobsAPI, job: BackgroundJobRecord):
        self._jobs = jobs_api
        self.job = job

    @property
    def job_id(self) -> int:
        return self.job.id

    @property
    def job_type(self) -> str:
        return self.job.job_type

    @property
    def author_type(self) -> str | None:
        return self.job.author_type

    @property
    def author_id(self) -> str | None:
        return self.job.author_id

    @property
    def ontology_id(self) -> int | None:
        return self.job.ontology_id

    @property
    def status(self) -> str:
        return self.job.status

    async def refresh(self) -> BackgroundJobRecord:
        self.job = await self._jobs.get(self.job.id)
        return self.job

    async def wait(self, **kwargs: Any) -> BackgroundJobRecord:
        self.job = await self._jobs.wait(self.job.id, **kwargs)
        return self.job

    def is_terminal(self) -> bool:
        return self.job.is_terminal

    def raise_if_failed(self) -> None:
        if self.job.failed:
            raise JobFailedError(self.job.id, self.job.error_message, self.job.details)


class OntologyEmbeddingsAPI:
    """Embedding-focused helper API built on generic jobs."""

    def __init__(self, client: AsyncShrecknetClient, jobs_api: JobsAPI):
        self._client = client
        self._jobs = jobs_api

    async def stats(self, ontology_id: int) -> EmbeddingStats:
        data = await self._client.raw_request("GET", f"/ontologies/{ontology_id}/embedding-stats")
        return EmbeddingStats.model_validate(data)

    async def recent_jobs(self, ontology_id: int, limit: int = 10) -> list[BackgroundJobRecord]:
        data = await self._client.raw_request("GET", f"/ontologies/{ontology_id}/embedding-jobs", params={"limit": limit})
        return [_normalize_job(item) for item in data]

    async def trigger(self, ontology_id: int, batch_size: int | None = None) -> JobHandle:
        payload = {} if batch_size is None else {"batch_size": batch_size}
        triggered = EmbeddingTriggerResponse.model_validate(
            await self._client.raw_request("POST", f"/ontologies/{ontology_id}/trigger-embedding", json=payload)
        )
        # Celery task id returned by trigger endpoint; map to concrete background job via recent jobs.
        recent = await self.recent_jobs(ontology_id, limit=10)
        match = next((j for j in recent if j.raw.get("celery_task_id") == triggered.job_id), None)
        if match is None and recent:
            match = recent[0]
        if match is None:
            # fallback pseudo-handle that can refresh only when real record exists later
            match = BackgroundJobRecord(id=-1, job_type="neo4j_embedding", status="queued", ontology_id=ontology_id, raw={"celery_task_id": triggered.job_id})
        return JobHandle(self._jobs, match)


class EmbeddingsAPI:
    """Full embeddings lifecycle API (ontology + GraphRAG)."""

    def __init__(self, client: AsyncShrecknetClient, ontology_embeddings: OntologyEmbeddingsAPI):
        self._client = client
        self._ontology = ontology_embeddings

    async def stats(self, ontology_id: int) -> EmbeddingStats:
        return await self._ontology.stats(ontology_id)

    async def trigger(self, ontology_id: int, batch_size: int | None = None) -> JobHandle:
        return await self._ontology.trigger(ontology_id, batch_size=batch_size)

    async def recent_jobs(self, ontology_id: int, limit: int = 10) -> list[BackgroundJobRecord]:
        return await self._ontology.recent_jobs(ontology_id, limit=limit)

    async def embed_node(self, node_id: str, ontology_id: int | None = None) -> GraphRAGEmbedNodeResult:
        data = await self._client.raw_request("POST", "/graphrag/embed/node", json={"node_id": node_id, "ontology_id": ontology_id})
        return GraphRAGEmbedNodeResult.model_validate(data)

    async def embed_ontology(self, ontology_id: int, batch_size: int = 50) -> GraphRAGEmbedOntologyResult:
        data = await self._client.raw_request("POST", "/graphrag/embed/ontology", json={"ontology_id": ontology_id, "batch_size": batch_size})
        return GraphRAGEmbedOntologyResult.model_validate(data)

    async def backfill_chunks(self, ontology_id: int, batch_size: int = 50) -> GraphRAGEmbedOntologyResult:
        data = await self._client.raw_request("POST", "/graphrag/embed/ontology/backfill-chunks", json={"ontology_id": ontology_id, "batch_size": batch_size})
        return GraphRAGEmbedOntologyResult.model_validate(data)

    async def reset_ontology_embeddings(self, ontology_id: int) -> GraphRAGResetEmbeddingsResult:
        data = await self._client.raw_request("POST", "/graphrag/embed/ontology/reset", json={"ontology_id": ontology_id})
        return GraphRAGResetEmbeddingsResult.model_validate(data)

    async def ensure_index(self) -> GraphRAGIndexStatus:
        data = await self._client.raw_request("POST", "/graphrag/index/ensure")
        return GraphRAGIndexStatus.model_validate(data)

    async def lifecycle_report(self, ontology_id: int) -> EmbeddingLifecycleReport:
        stats = await self.stats(ontology_id)
        return EmbeddingLifecycleReport(
            ontology_id=ontology_id,
            stats_available=True,
            total_nodes=stats.total_nodes,
            embedded_nodes=stats.embedded_nodes,
            unembedded_nodes=stats.unembedded_nodes,
            outdated_nodes=stats.outdated_nodes,
            entities=stats.entities,
            scenes=stats.scenes,
            milestones=stats.milestones,
        )


class ElderAPI:
    """Elder query and chat lifecycle endpoints."""

    def __init__(self, client: AsyncShrecknetClient, shreckllm: ShreckLLMAPI, agents: AgentsAPI, embeddings: EmbeddingsAPI):
        self._client = client
        self._shreckllm = shreckllm
        self._agents = agents
        self._embeddings = embeddings

    async def query(self, agent_id: str, request: ElderQueryRequest) -> ElderQueryResponse:
        data = await self._client.raw_request("POST", f"/jobs/elder/{agent_id}/query", json=request.model_dump(exclude_none=True))
        return ElderQueryResponse.model_validate(data)

    async def create_chat(self, payload: ElderChatCreate) -> ElderChatRead:
        data = await self._client.raw_request("POST", "/jobs/elder/chats/", json=payload.model_dump(exclude_none=True))
        return ElderChatRead.model_validate(data)

    async def list_chats(self, agent_id: str | None = None, limit: int = 100, offset: int = 0) -> ElderChatsList:
        params = {"limit": limit, "offset": offset}
        if agent_id is not None:
            params["agent_id"] = agent_id
        data = await self._client.raw_request("GET", "/jobs/elder/chats/", params=params)
        return ElderChatsList.model_validate(data)

    async def get_chat(self, chat_id: str, include_history: bool = False) -> ElderChatWithHistory:
        data = await self._client.raw_request("GET", f"/jobs/elder/chats/{chat_id}", params={"include_history": include_history})
        return ElderChatWithHistory.model_validate(data)

    async def update_chat(self, chat_id: str, payload: ElderChatUpdate) -> ElderChatRead:
        data = await self._client.raw_request("PATCH", f"/jobs/elder/chats/{chat_id}", json=payload.model_dump(exclude_none=True))
        return ElderChatRead.model_validate(data)

    async def delete_chat(self, chat_id: str) -> None:
        await self._client.raw_request("DELETE", f"/jobs/elder/chats/{chat_id}")

    async def get_chat_file(self, chat_id: str) -> dict[str, Any]:
        return await self._client.raw_request("GET", f"/jobs/elder/chats/{chat_id}/file")

    async def preflight(self, *, agent_id: str, ontology_id: int, strict: bool = False) -> ElderPreflightReport:
        reasons: list[str] = []

        llm_report = await self._shreckllm.preflight_agents_llm_ready(strict=False)
        llm_ready = llm_report.ready
        if not llm_ready:
            reasons.extend(llm_report.reasons)

        agent_ready = False
        try:
            agent = await self._agents.get(agent_id)
            if not agent.active:
                reasons.append("Agent is inactive")
            if agent.job != "elder":
                reasons.append(f"Agent job type is '{agent.job}', expected 'elder'")
            agent_ready = agent.active and agent.job == "elder"
        except Exception as exc:
            reasons.append(f"Agent lookup failed: {exc}")

        embedding_ready = False
        try:
            _ = await self._embeddings.lifecycle_report(ontology_id)
            embedding_ready = True
        except Exception as exc:
            reasons.append(f"Embedding readiness check failed: {exc}")

        report = ElderPreflightReport(
            ready=llm_ready and agent_ready and embedding_ready,
            reasons=reasons,
            llm_ready=llm_ready,
            agent_ready=agent_ready,
            embedding_ready=embedding_ready,
            provider_checks=llm_report.checks,
        )
        if strict and not report.ready:
            raise ElderPreflightError(reasons)
        return report


class ArchitectAPI:
    """Architect analyze/review/generate lifecycle endpoints."""

    def __init__(self, client: AsyncShrecknetClient, shreckllm: ShreckLLMAPI, agents: AgentsAPI, jobs: JobsAPI):
        self._client = client
        self._shreckllm = shreckllm
        self._agents = agents
        self._jobs = jobs

    async def analyze(self, agent_id: str, payload: ArchitectAnalysisRequest) -> ArchitectRunRead:
        data = await self._client.raw_request("POST", f"/jobs/architect/{agent_id}/analyze", json=payload.model_dump(exclude_none=True))
        return ArchitectRunRead.model_validate(data)

    async def get_run(self, run_id: str) -> ArchitectRunRead:
        data = await self._client.raw_request("GET", f"/jobs/architect/runs/{run_id}")
        return ArchitectRunRead.model_validate(data)

    async def list_runs(self, agent_id: str, limit: int = 20, offset: int = 0) -> list[ArchitectRunSummary]:
        data = await self._client.raw_request("GET", f"/jobs/architect/{agent_id}/runs", params={"limit": limit, "offset": offset})
        return [ArchitectRunSummary.model_validate(item) for item in data]

    async def delete_run(self, agent_id: str, run_id: str) -> dict[str, int]:
        return await self._client.raw_request("DELETE", f"/jobs/architect/{agent_id}/runs/{run_id}")

    async def delete_runs(self, agent_id: str) -> dict[str, int]:
        return await self._client.raw_request("DELETE", f"/jobs/architect/{agent_id}/runs")

    async def update_proposal_statuses(self, run_id: str, payload: ArchitectProposalStatusUpdate) -> dict[str, int]:
        return await self._client.raw_request("PATCH", f"/jobs/architect/runs/{run_id}/proposals/status", json=payload.model_dump(exclude_none=True))

    async def create_proposal(self, run_id: str, payload: ArchitectProposalCreate) -> ArchitectProposalRead:
        data = await self._client.raw_request("POST", f"/jobs/architect/runs/{run_id}/proposals", json=payload.model_dump(exclude_none=True))
        return ArchitectProposalRead.model_validate(data)

    async def patch_proposal(self, run_id: str, proposal_id: str, payload: ArchitectProposalUpdate) -> ArchitectProposalRead:
        data = await self._client.raw_request("PATCH", f"/jobs/architect/runs/{run_id}/proposals/{proposal_id}", json=payload.model_dump(exclude_none=True))
        return ArchitectProposalRead.model_validate(data)

    async def put_proposal(self, run_id: str, proposal_id: str, payload: ArchitectProposalUpdate) -> ArchitectProposalRead:
        data = await self._client.raw_request("PUT", f"/jobs/architect/runs/{run_id}/proposals/{proposal_id}", json=payload.model_dump(exclude_none=True))
        return ArchitectProposalRead.model_validate(data)

    async def generate(self, run_id: str, payload: ArchitectGenerationRequest) -> dict[str, Any]:
        return await self._client.raw_request("POST", f"/jobs/architect/runs/{run_id}/generate", json=payload.model_dump(exclude_none=True))

    async def wait_for_analysis(self, run_id: str, *, timeout_s: float = 600, poll_interval_s: float = 1.0, strict: bool = True) -> BackgroundJobRecord:
        started = time.monotonic()
        while True:
            run = await self.get_run(run_id)
            if run.background_job_id is not None:
                return await self._jobs.wait(run.background_job_id, timeout_s=max(1.0, timeout_s - (time.monotonic() - started)), poll_interval_s=poll_interval_s, strict=strict)
            if time.monotonic() - started >= timeout_s:
                raise JobTimeoutError(-1, timeout_s)
            await asyncio.sleep(poll_interval_s)

    async def wait_for_generation(self, run_id: str, *, timeout_s: float = 900, poll_interval_s: float = 1.0, strict: bool = True) -> BackgroundJobRecord:
        started = time.monotonic()
        while True:
            run = await self.get_run(run_id)
            if run.generation_job_id is not None:
                return await self._jobs.wait(run.generation_job_id, timeout_s=max(1.0, timeout_s - (time.monotonic() - started)), poll_interval_s=poll_interval_s, strict=strict)
            if time.monotonic() - started >= timeout_s:
                raise JobTimeoutError(-1, timeout_s)
            await asyncio.sleep(poll_interval_s)

    async def preflight(self, agent_id: str, *, strict: bool = False) -> ArchitectPreflightReport:
        reasons: list[str] = []

        llm_report = await self._shreckllm.preflight_agents_llm_ready(strict=False)
        llm_ready = llm_report.ready
        if not llm_ready:
            reasons.extend(llm_report.reasons)

        agent_ready = False
        try:
            agent = await self._agents.get(agent_id)
            if not agent.active:
                reasons.append("Agent is inactive")
            if agent.job != "architect":
                reasons.append(f"Agent job type is '{agent.job}', expected 'architect'")
            agent_ready = agent.active and agent.job == "architect"
        except Exception as exc:
            reasons.append(f"Agent lookup failed: {exc}")

        report = ArchitectPreflightReport(
            ready=llm_ready and agent_ready,
            reasons=reasons,
            llm_ready=llm_ready,
            agent_ready=agent_ready,
            provider_checks=llm_report.checks,
        )
        if strict and not report.ready:
            raise ArchitectPreflightError(reasons)
        return report
