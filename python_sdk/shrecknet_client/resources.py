from __future__ import annotations

from typing import Any

import httpx

from .client import AsyncShrecknetClient
from .errors import ConfigurationReadinessError
from .models import (
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
