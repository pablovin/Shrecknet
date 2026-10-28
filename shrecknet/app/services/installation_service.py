"""Resumable first deployment orchestration. Existing services own their data."""
from __future__ import annotations

import asyncio
import time
import hashlib
from uuid import uuid4
import httpx

from app.core.config_store import LLMModelTarget, get_settings, reload_settings, update_settings
from app.repositories.installation_repository import InstallationRepository
from app.schemas.installation import InstallationModels, InstallationState

JOB_TARGETS: dict[str, tuple[str, ...]] = {
    "elder": ("model_elder_planner", "model_elder_synthesis", "model_elder_character_incorporation"),
    "architect": ("model_architect_scene_chunking", "model_architect_entity_proposal", "model_architect_milestone_proposal", "model_architect_entity_generation"),
    "novelist": ("model_novelist_analysis", "model_novelist_writer"),
    "librarian": ("model_librarian_planner", "model_librarian_synthesis", "model_librarian_character_incorporation"),
    "character_agent": ("model_character_agent_character_incorporation", "model_character_agent_scene_interpretation", "model_character_agent_psychological_interpretation", "model_character_agent_decision_making"),
    "companion": ("model_companion_routing", "model_companion_synthesis", "model_companion_interim", "model_companion_rapport_update"),
}

class InstallationConflict(ValueError):
    pass

class InstallationService:
    def __init__(self, repository: InstallationRepository | None = None):
        self.repository = repository or InstallationRepository()

    @staticmethod
    def fingerprint(keys: list[str]) -> str:
        settings = reload_settings()
        value = "|".join(f"{key}:{getattr(settings, key).model_dump_json()}" for key in sorted(keys))
        return hashlib.sha256(value.encode()).hexdigest()

    def status(self) -> InstallationState:
        with self.repository.transaction() as (_, state):
            if state.verification_status in {"queued", "running"} and time.time() - (state.verification_started_at or 0) > 600:
                state.verification_status = "failed"
                state.verification_code = "worker_timeout"
            if state.agents_configured and state.model_fingerprint != self.fingerprint(state.verified_targets):
                state.verification_status = "not_started"
                state.verification_id = None
                state.verification_code = "model_configuration_changed"
                state.completed = False
            return state

    async def configure_models(self, payload: InstallationModels, token: str, agent_service=None) -> InstallationState:
        # Validate availability using the gateway's authoritative catalog.
        settings = get_settings()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{settings.shreckllm_base_url.rstrip('/')}/providers/{payload.provider}/models", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            catalog = response.json()
        if payload.model not in catalog.get("configured_models", []):
            raise ValueError("model_not_configured")
        keys = {key for job in payload.jobs for key in JOB_TARGETS[job]} | {"model_agents_repair_json"}
        target = LLMModelTarget(provider=payload.provider, name=payload.model)
        from app.core.installation_claim import registration_guard
        from app.schemas.agent import AgentCreate
        async with registration_guard("installation-models"):
            current = self.status()
            if current.verification_status in {"queued", "running"}:
                raise InstallationConflict("verification_pending")
            if not current.world_id:
                raise InstallationConflict("world_required")
            if agent_service is not None:
                for job in payload.jobs:
                    if job not in {"elder", "architect", "novelist", "librarian"}:
                        continue  # Embodied/personal agents need their domain inputs.
                    existing = await agent_service.repository.first_world_job(current.world_id, job)
                    agent = existing or await agent_service.create_agent(AgentCreate(
                        name=f"{current.world_name} {job.title()}", job=job,
                        description="Created during guided installation.", ontology_ids=[current.world_id],
                    ))
                    with self.repository.transaction() as (_, saved):
                        saved.agent_ids[job] = agent.id
            return self._save_models(keys, target)

    def _save_models(self, keys: set[str], target: LLMModelTarget) -> InstallationState:
        with self.repository.transaction() as (_, state):
            if state.verification_status in {"queued", "running"}:
                raise InstallationConflict("verification_pending")
            update_settings({**{key: target.model_dump() for key in keys}, "enable_ai_agents": True})
            state.agents_configured = True
            state.verified_targets = sorted(set(state.verified_targets) | keys)
            state.model_fingerprint = self.fingerprint(state.verified_targets)
            state.verification_status = "not_started"
            state.verification_id = None
            state.verification_code = None
            state.completed = False
            return state

    def begin_verification(self) -> tuple[InstallationState, bool]:
        # Atomic reservation means repeat clicks do not enqueue duplicate paid work.
        self.status()
        with self.repository.transaction() as (_, state):
            if not state.agents_configured:
                raise InstallationConflict("agents_not_configured")
            if state.verification_status in {"queued", "running", "passed"}:
                return state, False
            state.verification_id = str(uuid4())
            state.verification_status = "queued"
            state.verification_code = None
            state.verification_started_at = time.time()
            return state, True

    def finish(self) -> InstallationState:
        self.status()
        with self.repository.transaction() as (_, state):
            if not state.world_id or not state.tour_completed:
                raise InstallationConflict("setup_incomplete")
            if state.agents_configured and (state.verification_status != "passed" or state.model_fingerprint != self.fingerprint(state.verified_targets)):
                raise InstallationConflict("verification_required")
            state.completed = True
            return state

    async def verify_models(self, verification_id: str) -> None:
        with self.repository.transaction() as (_, state):
            if state.verification_id != verification_id or state.verification_status != "queued":
                return
            state.verification_status = "running"
            keys = list(state.verified_targets)
        code = None
        try:
            from app.integrations.llm.shreckllm_client import ShreckLLMClient
            settings = get_settings()
            # This verifies the queue, worker and model path. It does not label
            # domain retrieval, ingestion, or agent pipelines as verified.
            async def check_targets():
                async with ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=20, max_retries=0) as client:
                    targets = {getattr(settings, key).model_dump_json(): getattr(settings, key) for key in keys}
                    for target in targets.values():
                        answer = await client.chat(model=target, messages=[{"role": "user", "content": "Reply with a short greeting."}], max_tokens=32, usage_tag="installation.model_check")
                        if not isinstance(answer, str) or not answer.strip():
                            raise ValueError("empty_model_response")
            await asyncio.wait_for(check_targets(), timeout=480)
        except Exception:
            code = "model_check_failed"
        with self.repository.transaction() as (_, state):
            if state.verification_id == verification_id and state.verification_status == "running":
                if state.model_fingerprint != self.fingerprint(state.verified_targets):
                    code = "model_configuration_changed"
                state.verification_status = "failed" if code else "passed"
                state.verification_code = code


    async def create_world(self, template, setup_service, instance_service=None, include_example: bool = True) -> InstallationState:
        from app.core.installation_claim import registration_guard
        async with registration_guard("installation-world"):
            state = self.repository.read()
            if state.world_id:
                return state
            if state.world_template and state.world_template != template:
                raise InstallationConflict("resume_selected_world")
            if not state.world_template:
                existing = await setup_service.ontology_service.repository.get_by_name(template)
                if existing is not None:
                    raise InstallationConflict("world_name_exists")
                with self.repository.transaction() as (_, saved):
                    saved.world_template = template
            result = await setup_service.resume_default_world(template)
            example_page_id = example_scene_id = None
            if include_example and instance_service is not None:
                from app.services.installation_example import ensure_example
                definitions = await setup_service.ontology_service.repository.list_entities(result.ontology_id)
                example_page_id, example_scene_id = await ensure_example(result.ontology_id, instance_service, definitions)
                # Semantic indexing is idempotent and uses the normal worker.
                from app.tasks.neo4j_embedding import embed_instance
                embed_instance.apply_async(args=[example_page_id], queue="ontology_linking")
            with self.repository.transaction() as (_, state):
                state.example_page_id = example_page_id
                state.example_scene_id = example_scene_id
                state.world_id = result.ontology_id
                state.world_name = result.name
                return state
