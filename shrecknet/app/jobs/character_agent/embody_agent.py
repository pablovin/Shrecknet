"""Scene-centric source-boundary CharacterAgent embodiment generation.

Three LLM calls run for each source group:
  1. Character incorporation
  2. Scene psychological enrichment and scene-local candidate extraction
  3. Evidence-grounded trait, aspect, and goal update

Between stages 2 and 3 the backend validates and grounds the extracted
candidates; this is deterministic work, not a fourth LLM call. All calls process
every ordered scene from one source using its starting revision. Source bundles
run sequentially and accumulate grounded evidence; each produces one
scene-associated revision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.integrations.llm.json_repair import repair_json_text
from app.integrations.llm.structured_output import (
    strict_json_schema,
    structured_output_is_unsupported,
)
from app.integrations.llm.shreckllm_client import LLMProviderUnavailableError
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.embody_agent_prompts import (
    BASELINE_PROMPT,
    IDENTITY_SIGNALS_PROMPT,
    TRAIT_ENRICHMENT_PROMPT,
    PERSPECTIVE_PROMPT,
)
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    EmbodyAgentResult,
    LLMCallRecord,
    ProfileUpdateOutput,
    SceneInput,
    SceneIdentitySignalsOutput,
    SceneEnrichmentsOutput,
    ScenePerspectiveBundleOutput,
    ScenePerspectiveOutput,
    SubtitleChangeProposal,
)


from app.schemas.character_traits import TraitProfile, TraitEvidence, TraitProposal
from app.services.character_trait_service import (
    ground_observations, merge_evidence, update_profile, validate_proposals, validate_scene_grounding, scene_digest,
)

logger = logging.getLogger(__name__)


class EmbodimentGenerationError(RuntimeError):
    """Categorized failure from one embodiment generation stage."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "generation",
        stage: str | None = None,
        source_entity_id: str | None = None,
        source_entity_alias: str | None = None,
        offending_ids: set[str] | None = None,
        allowed_ids: set[str] | None = None,
        expected_sequence: list[str] | None = None,
        actual_sequence: list[str] | None = None,
        attempt: int = 1,
        retryable: bool = False,
        provider_id: str | None = None,
        model_name: str | None = None,
        provider_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.source_entity_id = source_entity_id
        self.source_entity_alias = source_entity_alias
        self.offending_ids = sorted(offending_ids or set())
        self.allowed_ids = sorted(allowed_ids or set())
        self.expected_sequence = list(expected_sequence or [])
        self.actual_sequence = list(actual_sequence or [])
        self.attempt = attempt
        self.retryable = retryable
        self.provider_id = provider_id
        self.model_name = model_name
        self.provider_reason = provider_reason

    def details(self) -> dict[str, Any]:
        return {
            "failure_category": self.category,
            "failed_stage": self.stage,
            "failed_source_id": self.source_entity_id,
            "failed_source_alias": self.source_entity_alias,
            "attempt": self.attempt,
            "retryable": self.retryable,
            "offending_ids": self.offending_ids,
            "allowed_ids": self.allowed_ids,
            "expected_sequence": self.expected_sequence,
            "actual_sequence": self.actual_sequence,
            "provider_id": self.provider_id,
            "model": self.model_name,
            "provider_reason": self.provider_reason,
        }


class _PerspectivesContainer(BaseModel):
    perspectives: list[ScenePerspectiveOutput]


class UsageTracker:
    def __init__(self, llm_client):
        self.llm = llm_client
        self.calls: list[LLMCallRecord] = []
        self.stage_elapsed_seconds: dict[str, float] = {}

    async def chat(self, *, stage: str, usage_tag: str, **kwargs) -> str:
        input_text = json.dumps(kwargs.get("messages", []), ensure_ascii=False)
        input_chars = len(input_text)
        input_tokens_est = max(1, input_chars // 4)

        started_at = time.monotonic()
        result = await self.llm.chat(usage_tag=usage_tag, **kwargs)
        elapsed_seconds = time.monotonic() - started_at
        self.stage_elapsed_seconds[stage] = (
            self.stage_elapsed_seconds.get(stage, 0.0) + elapsed_seconds
        )

        output_chars = len(str(result))
        output_tokens_est = max(1, output_chars // 4)

        target = kwargs.get("model")
        self.calls.append(LLMCallRecord(
            stage=stage,
            usage_tag=usage_tag,
            provider=str(getattr(target, "provider", "openai")),
            model=str(getattr(target, "name", target)),
            input_chars=input_chars,
            output_chars=output_chars,
            input_tokens_est=input_tokens_est,
            output_tokens_est=output_tokens_est,
            total_tokens_est=input_tokens_est + output_tokens_est,
        ))
        return result


class EmbodyAgent:
    def __init__(
        self, *, llm_client, character_incorporation_model,
        scene_interpretation_model, character_update_model,
        max_goals: int = 10, max_aspects: int = 20,
        semantic_correction_attempts: int = 1,
        debug_artifacts: EmbodimentDebugArtifacts | None = None,
        debug_source_index: int | None = None,
        debug_source_alias: str | None = None,
    ):
        self._llm = UsageTracker(llm_client)
        self.character_incorporation_model = character_incorporation_model
        self.scene_interpretation_model = scene_interpretation_model
        self.character_update_model = character_update_model
        self.max_goals = max_goals
        self.max_aspects = max_aspects
        self.semantic_correction_attempts = semantic_correction_attempts
        self.semantic_correction_count = 0
        self._debug_artifacts = debug_artifacts
        self._debug_source_index = debug_source_index
        self._debug_source_alias = debug_source_alias

    @property
    def llm_calls(self) -> list[LLMCallRecord]:
        return self._llm.calls

    @property
    def stage_elapsed_seconds(self) -> dict[str, float]:
        return dict(self._llm.stage_elapsed_seconds)

    def _debug_call(self, **values: Any) -> None:
        if self._debug_artifacts is not None:
            values.setdefault(
                "response_metadata",
                dict(getattr(self._llm.llm, "last_response_metadata", {}) or {}),
            )
            self._debug_artifacts.write_call(
                source_index=self._debug_source_index,
                source_alias=self._debug_source_alias,
                **values,
            )

    async def _chat_structured(
        self, *, schema: type[BaseModel], stage: str, usage_tag: str,
        model: Any, messages: list[dict[str, str]], max_tokens: int | None,
        output_binding: dict[str, Any] | None = None,
    ) -> str:
        """Request provider-native JSON Schema, with an explicit compatibility fallback."""
        response_format = strict_json_schema(
            schema.__name__.removeprefix("_"),
            _model_output_schema(schema, output_binding),
        )
        try:
            return await self._llm.chat(
                stage=stage, usage_tag=usage_tag, model=model, messages=messages,
                temperature=0.0, max_tokens=max_tokens, response_format=response_format,
            )
        except Exception as exc:
            if not structured_output_is_unsupported(exc):
                raise
            return await self._llm.chat(
                stage=stage, usage_tag=f"{usage_tag}.structured_fallback", model=model,
                messages=messages, temperature=0.0, max_tokens=max_tokens,
            )

    @staticmethod
    def _parse(
        schema: type[BaseModel], raw: str, stage: str,
        output_binding: dict[str, Any] | None = None,
    ) -> BaseModel:
        try:
            parsed = parse_json_deterministically(raw)
            parsed = _normalize_position_bound_collection(parsed, output_binding)
            if output_binding:
                _bind_model_output_references(parsed, **output_binding)
        except (TypeError, ValueError) as exc:
            raise EmbodimentGenerationError(
                f"invalid JSON in {stage} output", category="json",
            ) from exc
        try:
            dropped = _drop_ungrounded_output_items(parsed, schema=schema)
            if dropped:
                logger.warning(
                    "embodiment_ungrounded_items_dropped stage=%s count=%d",
                    stage, dropped,
                )
            return schema.model_validate(parsed)
        except (TypeError, ValueError, ValidationError) as exc:
            raise EmbodimentGenerationError(
                f"invalid {stage} output", category="schema",
            ) from exc

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _schema_errors(error: EmbodimentGenerationError) -> list[dict[str, Any]]:
        """Expose JSON-safe parser errors to a correction call without inventing evidence."""
        cause = error.__cause__
        if isinstance(cause, ValidationError):
            # Pydantic includes the original exception in ``ctx.error`` for
            # model-validator failures. A correction request is JSON, so retain
            # the error text while removing non-serializable exception objects.
            return json.loads(json.dumps(cause.errors(include_url=False), default=str))
        return [{"message": str(error)}]

    async def _call(
        self, *, prompt: str, payload: dict[str, Any], schema: type[BaseModel],
        stage: str, usage_tag: str, max_tokens: int | None, model: Any,
        semantic_validator: Callable[[BaseModel], None] | None = None,
        source_entity_id: str | None = None,
        source_entity_alias: str | None = None,
        schema_correction_attempts: int = 0,
        output_binding: dict[str, Any] | None = None,
    ) -> BaseModel:
        try:
            raw = await self._chat_structured(
                schema=schema,
                stage=stage,
                usage_tag=usage_tag,
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": self._json(payload)},
                ],
                max_tokens=max_tokens,
                output_binding=output_binding,
            )
        except LLMProviderUnavailableError as exc:
            self._debug_call(stage=stage, prompt=prompt, payload=payload, error=str(exc),
                             model=model, usage_tag=usage_tag)
            raise EmbodimentGenerationError(
                f"{stage} provider is unavailable",
                category="provider_unavailable",
                stage=stage,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                provider_id=exc.provider_id,
                model_name=str(getattr(model, "name", model)),
                provider_reason=exc.reason,
            ) from exc
        except Exception as exc:
            self._debug_call(stage=stage, prompt=prompt, payload=payload, error=str(exc),
                             model=model, usage_tag=usage_tag)
            raise EmbodimentGenerationError(
                f"{stage} transport failed",
                category="transport",
                stage=stage,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                retryable=True,
            ) from exc
        if not str(raw).strip():
            self._debug_call(
                stage=stage, prompt=prompt, payload=payload, raw_output=raw,
                error="provider returned an empty response body", model=model,
                usage_tag=usage_tag,
            )
            raise EmbodimentGenerationError(
                f"{stage} returned an empty response",
                category="empty_response",
                stage=stage,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                retryable=True,
            )
        try:
            result = self._parse(schema, str(raw), stage, output_binding)
            self._debug_call(stage=stage, prompt=prompt, payload=payload, raw_output=raw,
                             parsed_output=result, model=model, usage_tag=usage_tag)
        except EmbodimentGenerationError as exc:
            self._debug_call(stage=stage, prompt=prompt, payload=payload, raw_output=raw,
                             error=self._schema_errors(exc), model=model, usage_tag=usage_tag)
            response_metadata = getattr(self._llm.llm, "last_response_metadata", {})
            logger.warning(
                "embodiment_schema_invalid stage=%s source_id=%s source_alias=%s "
                "requested_max_tokens=%s response_chars=%s completion_tokens=%s "
                "finish_reason=%s validation_errors=%s",
                stage, source_entity_id, source_entity_alias, max_tokens, len(str(raw)),
                response_metadata.get("completion_tokens"),
                response_metadata.get("finish_reason"), self._schema_errors(exc),
            )
            last_error = exc
            repair_succeeded = False
            if exc.category == "json":
                try:
                    repaired = await repair_json_text(
                        llm_client=self._llm.llm, model=model,
                        malformed_text=str(raw),
                        schema_hint=json.dumps(_model_output_schema(schema, output_binding)),
                        response_format=strict_json_schema(
                            schema.__name__.removeprefix("_"),
                            _model_output_schema(schema, output_binding),
                        ),
                        usage_tag=f"{usage_tag}.repair",
                    )
                    result = self._parse(schema, repaired, f"repaired {stage}", output_binding)
                    self._debug_call(stage=stage, prompt="JSON repair", payload={
                        "malformed_text": str(raw), "required_output_schema": _model_output_schema(schema, output_binding),
                    }, raw_output=repaired, parsed_output=result, model=model,
                        usage_tag=f"{usage_tag}.repair", call_kind="json_repair")
                    repair_succeeded = True
                except Exception as repaired_exc:
                    self._debug_call(stage=stage, prompt="JSON repair", payload={
                        "malformed_text": str(raw), "required_output_schema": _model_output_schema(schema, output_binding),
                    }, error=str(repaired_exc), model=model, usage_tag=f"{usage_tag}.repair",
                        call_kind="json_repair")
                    if isinstance(repaired_exc, EmbodimentGenerationError):
                        last_error = repaired_exc
            for attempt in range(
                1, (schema_correction_attempts if not repair_succeeded else 0) + 1,
            ):
                corrected_raw = None
                correction_payload = {
                    "original_input": payload,
                    "rejected_output": str(raw),
                    "validation_errors": self._schema_errors(last_error),
                    "required_output_schema": _model_output_schema(schema, output_binding),
                    "instruction": (
                        "Return one complete replacement object that satisfies the "
                        "required output schema. Do not invent evidence. Preserve "
                        "valid items; for every required list with no qualified "
                        "item, return an explicit empty list. Return JSON only."
                    ),
                }
                try:
                    corrected_raw = await self._chat_structured(
                        schema=schema,
                        stage=stage,
                        usage_tag=f"{usage_tag}.schema_correction",
                        model=model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": self._json(correction_payload)},
                        ],
                        max_tokens=max_tokens,
                        output_binding=output_binding,
                    )
                    result = self._parse(schema, str(corrected_raw), f"schema corrected {stage}", output_binding)
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     raw_output=corrected_raw, parsed_output=result, model=model,
                                     usage_tag=f"{usage_tag}.schema_correction",
                                     call_kind="schema_correction")
                    break
                except EmbodimentGenerationError as corrected_exc:
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     raw_output=corrected_raw,
                                     error=self._schema_errors(corrected_exc), model=model,
                                     usage_tag=f"{usage_tag}.schema_correction",
                                     call_kind="schema_correction")
                    last_error = corrected_exc
                except Exception as corrected_exc:
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     error=str(corrected_exc), model=model,
                                     usage_tag=f"{usage_tag}.schema_correction",
                                     call_kind="schema_correction")
                    logger.warning(
                        "embodiment_schema_correction_transport_failed stage=%s attempt=%d error=%s",
                        stage, attempt, corrected_exc,
                    )
            else:
                if not repair_succeeded:
                    raise EmbodimentGenerationError(
                        f"{stage} schema validation failed",
                        category="schema",
                        stage=stage,
                        source_entity_id=source_entity_id,
                        source_entity_alias=source_entity_alias,
                        retryable=True,
                    ) from last_error

        if semantic_validator is None:
            return result

        for correction_index in range(self.semantic_correction_attempts + 1):
            try:
                semantic_validator(result)
                return result
            except EmbodimentGenerationError as exc:
                attempt = correction_index + 1
                exc.stage = exc.stage or stage
                exc.source_entity_id = exc.source_entity_id or source_entity_id
                exc.source_entity_alias = exc.source_entity_alias or source_entity_alias
                exc.attempt = attempt
                exc.retryable = correction_index < self.semantic_correction_attempts
                logger.warning(
                    "embodiment_stage_validation_failed stage=%s source_id=%s "
                    "source_alias=%s model=%s attempt=%d category=%s "
                    "offending_ids=%s allowed_ids=%s retryable=%s",
                    stage, source_entity_id, source_entity_alias,
                    str(getattr(model, "name", model)), attempt, exc.category,
                    exc.offending_ids, exc.allowed_ids, exc.retryable,
                )
                self._debug_call(
                    stage=stage, prompt=prompt, payload=payload, raw_output=raw,
                    error=exc.details(), model=model, usage_tag=usage_tag,
                    call_kind="semantic_validation",
                )
                if not exc.retryable:
                    raise
                self.semantic_correction_count += 1
                correction_payload = {
                    "original_input": payload,
                    "rejected_output": result.model_dump(mode="json"),
                    "validation_error": exc.details(),
                    "instruction": (
                        "Correct only the validation errors. Return the complete "
                        "replacement object using the original output contract."
                    ),
                }
                try:
                    corrected_raw = await self._chat_structured(
                        schema=schema,
                        stage=stage,
                        usage_tag=f"{usage_tag}.semantic_correction",
                        model=model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": self._json(correction_payload)},
                        ],
                        max_tokens=max_tokens,
                        output_binding=output_binding,
                    )
                except Exception as correction_exc:
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     error=str(correction_exc), model=model,
                                     usage_tag=f"{usage_tag}.semantic_correction",
                                     call_kind="semantic_correction")
                    raise EmbodimentGenerationError(
                        f"{stage} semantic correction transport failed",
                        category="transport",
                        stage=stage,
                        source_entity_id=source_entity_id,
                        source_entity_alias=source_entity_alias,
                        attempt=attempt + 1,
                        retryable=True,
                    ) from correction_exc
                try:
                    result = self._parse(
                        schema, str(corrected_raw), f"corrected {stage}", output_binding
                    )
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     raw_output=corrected_raw, parsed_output=result, model=model,
                                     usage_tag=f"{usage_tag}.semantic_correction",
                                     call_kind="semantic_correction")
                except EmbodimentGenerationError as corrected_exc:
                    self._debug_call(stage=stage, prompt=prompt, payload=correction_payload,
                                     raw_output=corrected_raw, error=self._schema_errors(corrected_exc),
                                     model=model, usage_tag=f"{usage_tag}.semantic_correction",
                                     call_kind="semantic_correction")
                    raise EmbodimentGenerationError(
                        f"{stage} correction schema validation failed",
                        category="schema",
                        stage=stage,
                        source_entity_id=source_entity_id,
                        source_entity_alias=source_entity_alias,
                        attempt=attempt + 1,
                        retryable=False,
                    ) from corrected_exc

        raise AssertionError("semantic validation loop did not return or raise")

    async def initialize(self, *, canonical_identity: dict[str, Any], entity_id: str):
        evidence_id = f"identity:{entity_id}"
        known = {evidence_id}
        def validate(value):
            _validate_and_normalize_evidence(value, allowed_ids=known, stage="authored baseline")
            _semantic(lambda: ground_observations(value.trait_evidence, scene_ids=[],
                source_group_id=entity_id, authored_evidence_ids=known))
        output = await self._call(
            prompt=BASELINE_PROMPT,
            payload={"identity": {key: canonical_identity.get(key) for key in
                ("alias", "authored_text", "properties", "entity_type")}, "allowed_evidence_ids": [evidence_id]},
            schema=EmbodimentObservationsOutput, stage="authored baseline",
            usage_tag="character_agent.embodiment.baseline", max_tokens=None,
            model=self.scene_interpretation_model, semantic_validator=validate,
            output_binding={"evidence_id": evidence_id},
        )
        evidence = ground_observations(output.trait_evidence, scene_ids=[],
            source_group_id=entity_id, authored_evidence_ids=known)
        proposals = [TraitProposal(trait=item.trait,
            observation_ids=[item.id], justification=item.justification,
            addresses_contradictions="Authored baseline only.") for item in evidence if item.eligible]
        profile, _ = update_profile(TraitProfile(), evidence, proposals)
        return profile, evidence, output

    async def generate_perspectives(
        self,
        *,
        source_entity_id: str,
        source_entity_alias: str,
        canonical_identity: dict[str, Any],
        current_trait_profile: TraitProfile,
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        scenes: list[SceneInput],
    ) -> _PerspectivesContainer:
        """Run the flat first wave for one chunk; it never starts another LLM call."""
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")
        expected_ids = [scene.scene_id for scene in scenes]
        result = await self._call(
            prompt=PERSPECTIVE_PROMPT,
            payload={
                "identity": {key: canonical_identity.get(key) for key in (
                    "alias", "subtitle", "entity_type", "entity_type_description", "properties",
                )},
                "current_profile": {
                    "trait_profile": current_trait_profile.model_dump(mode="json"),
                    "aspects": [{"name": item.get("name", ""), "category": item.get("category", ""), "description": item.get("description")} for item in current_aspects],
                    "goals": [{"title": item.get("title", ""), "description": item.get("description", ""), "goal_type": item.get("goal_type", "")} for item in current_goals],
                },
                "scenes": [{"position": index + 1, "name": scene.name, "description": scene.description, "created_at": scene.created_at} for index, scene in enumerate(scenes)],
            },
            schema=_PerspectivesContainer,
            semantic_validator=lambda value: _semantic(
                lambda: _validate_and_normalize_scene_grounding(value.perspectives, expected_ids)
            ),
            stage="character incorporation",
            usage_tag="character_agent.embodiment.perspective",
            max_tokens=None,
            model=self.character_incorporation_model,
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            schema_correction_attempts=0,
            output_binding={"collection": "perspectives", "scene_ids": expected_ids},
        )
        return result

    async def analyze(
        self,
        *,
        source_entity_id: str,
        source_entity_alias: str,
        canonical_identity: dict[str, Any],
        current_trait_profile: TraitProfile,
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        scenes: list[SceneInput],
        on_stage: Any = None,
        stage_checkpoints: dict[str, dict[str, Any]] | None = None,
        on_checkpoint: Any = None,
        perspectives_result: _PerspectivesContainer | None = None,
    ) -> EmbodyAgentAnalysis:
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")

        scene_list = [
            {"position": index + 1, "name": scene.name, "description": scene.description,
             "created_at": scene.created_at}
            for index, scene in enumerate(scenes)
        ]
        known = {f"scene:{s.scene_id}" for s in scenes}
        stage_checkpoints = stage_checkpoints or {}

        identity = {
            key: canonical_identity.get(key)
            for key in (
                "alias", "subtitle", "entity_type",
                "entity_type_description", "properties",
            )
        }
        aspects = [
            {
                "id": str(a.get("id") or ""),
                "name": a.get("name", ""),
                "category": a.get("category", ""),
                "description": a.get("description"),
            }
            for a in current_aspects
        ]
        goals = [
            {
                "id": str(g.get("id") or ""),
                "title": g.get("title", ""),
                "description": g.get("description", ""),
                "goal_type": g.get("goal_type", ""),
            }
            for g in current_goals
        ]
        if any(not item["id"] for item in aspects + goals):
            raise EmbodimentGenerationError("current profile entries require stable ids")

        # Step 1 — Character incorporation
        if on_stage:
            await on_stage("source:{0} - Step 1: Character incorporation".format(source_entity_alias), [1])
        if perspectives_result is not None:
            pass
        elif "character_incorporation" in stage_checkpoints:
            perspectives_result = _PerspectivesContainer.model_validate(
                stage_checkpoints["character_incorporation"]
            )
        else:
            perspectives_result = await self._call(
                prompt=PERSPECTIVE_PROMPT,
                payload={
                    "identity": identity,
                    "current_profile": {
                        "trait_profile": current_trait_profile.model_dump(mode="json"),
                        "aspects": [{"name": item["name"], "category": item["category"], "description": item["description"]} for item in aspects],
                        "goals": [{"title": item["title"], "description": item["description"], "goal_type": item["goal_type"]} for item in goals],
                    },
                    "scenes": scene_list,
                },
                schema=_PerspectivesContainer,
                semantic_validator=lambda value: _semantic(
                    lambda: _validate_and_normalize_scene_grounding(
                        value.perspectives, [s.scene_id for s in scenes]
                    )
                ),
                stage="character incorporation",
                usage_tag="character_agent.embodiment.character_incorporation",
                max_tokens=None,
                model=self.character_incorporation_model,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                schema_correction_attempts=1,
                output_binding={"collection": "perspectives", "scene_ids": [s.scene_id for s in scenes]},
            )
        perspectives = perspectives_result.perspectives
        expected_ids = [s.scene_id for s in scenes]
        actual_ids = [p.scene_id for p in perspectives]
        if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
            raise EmbodimentGenerationError(
                "perspective output scene_ids must match input scene order and be unique"
            )
        _semantic(lambda: _validate_and_normalize_scene_grounding(perspectives, expected_ids))
        if "character_incorporation" not in stage_checkpoints and on_checkpoint:
            await on_checkpoint("character_incorporation", perspectives_result)

        # Step 2 — Per-scene psychological enrichment. It consumes only the
        # already-grounded character perspective, never the raw objective scene.
        # Reflection remains presentation-only and cannot manufacture evidence.
        if on_stage:
            await on_stage(
                "source:{0} - Step 2: Psychological analysis".format(source_entity_alias), [2]
            )
        trait_payload = {
            "perspectives": [
                p.model_dump(
                    mode="json",
                    exclude={"scene_id", "evidence_ids", "character_reflection", "status"},
                ) | {"position": index + 1}
                for index, p in enumerate(perspectives)
            ],
            "current_profile": {
                "aspects": [{"position": index + 1, "name": a["name"]} for index, a in enumerate(aspects)],
                "goals": [{"position": index + 1, "title": g["title"]} for index, g in enumerate(goals)],
            },
        }
        enrichment_result = (
            SceneEnrichmentsOutput.model_validate(stage_checkpoints["scene_interpretation"])
            if "scene_interpretation" in stage_checkpoints
            else None
        )
        if enrichment_result is None:
            enrichment_result = await self._call(
                    prompt=TRAIT_ENRICHMENT_PROMPT, payload=trait_payload,
                    schema=SceneEnrichmentsOutput,
                    semantic_validator=lambda value: _semantic(
                        lambda: _validate_and_normalize_scene_grounding(
                            value.scene_enrichments, expected_ids
                        )
                    ),
                    stage="scene trait extraction",
                    usage_tag="character_agent.embodiment.scene_interpretation",
                    max_tokens=None, model=self.scene_interpretation_model,
                    source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                    schema_correction_attempts=0,
                    output_binding={
                        "collection": "scene_enrichments", "scene_ids": expected_ids,
                        "profile_targets": {"aspects": [a["id"] for a in aspects], "goals": [g["id"] for g in goals]},
                    },
                )
            if on_checkpoint:
                await on_checkpoint("scene_interpretation", enrichment_result)
        assert enrichment_result is not None
        enrichments = enrichment_result.scene_enrichments
        enrichment_ids = [item.scene_id for item in enrichments]
        if enrichment_ids != expected_ids or len(enrichment_ids) != len(set(enrichment_ids)):
            raise EmbodimentGenerationError(
                "trait extraction output scene_ids must match input scene order and be unique"
            )
        _semantic(lambda: _validate_and_normalize_scene_grounding(enrichments, expected_ids))
        aspect_ids = {item["id"] for item in aspects}
        goal_ids = {item["id"] for item in goals}
        for enrichment in enrichments:
            for impact in enrichment.impacts:
                permitted_ids = goal_ids if impact.impact_type.value == "goal_change" else aspect_ids
                if impact.target_id not in permitted_ids:
                    raise EmbodimentGenerationError(
                        "scene trait extraction referenced an unknown profile target"
                    )

        bundles = [
            ScenePerspectiveBundleOutput(
                **perspective.model_dump(mode="json"),
                emotions=enrichment.emotions,
                beliefs=enrichment.beliefs,
                impacts=enrichment.impacts,
                trait_candidates=enrichment.trait_candidates,
                aspect_signals=enrichment.aspect_signals,
                goal_signals=enrichment.goal_signals,
            )
            for perspective, enrichment in zip(perspectives, enrichments, strict=True)
        ]

        # The two second-wave branches already contain all source-local evidence.
        # Construct observations in the backend so no additional reconstruction
        # call can reintroduce raw scenes or an accumulated evidence history.
        observations = EmbodimentObservationsOutput(
            trait_evidence=[
                candidate
                for enrichment in enrichments
                for candidate in enrichment.trait_candidates
            ],
        )
        _validate_and_normalize_evidence(
            observations, allowed_ids=known, stage="scene-local trait candidates",
        )
        _semantic(lambda: ground_observations(
            observations.trait_evidence,
            scene_ids=expected_ids,
            source_group_id=source_entity_id,
        ))

        return EmbodyAgentAnalysis(
            scene_input_digests={s.scene_id: scene_digest(s.model_dump()) for s in scenes},
            source_entity_id=source_entity_id,
            source_entity_alias=str(source_entity_alias),
            perspectives=bundles,
            observations=observations,
            subtitle_change=observations.subtitle_change or SubtitleChangeProposal(),
            evidence_ids=known,
            aspect_signals=[
                signal for item in enrichments for signal in item.aspect_signals
            ],
            goal_signals=[
                signal for item in enrichments for signal in item.goal_signals
            ],
            llm_calls=list(self.llm_calls),
            observations_unavailable=False,
        )

    async def apply_profile_update(
        self,
        *,
        analysis: EmbodyAgentAnalysis,
        current_trait_profile: TraitProfile,
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        current_trait_evidence: list[TraitEvidence] | None = None,
        batch_id: str | None = None,
        stage_checkpoint: dict | None = None,
        on_checkpoint: Any = None,
        on_stage: Any = None,
    ) -> EmbodyAgentResult:
        """Apply one analyzed source to the latest chronological profile."""

        if analysis.observations_unavailable:
            # The source remains represented by perspectives and a no-change
            # revision, but malformed observations can never create evidence or
            # mutate traits, aspects, goals, or subtitle state.
            return EmbodyAgentResult(
                scene_input_digests=analysis.scene_input_digests,
                source_entity_id=analysis.source_entity_id,
                source_entity_alias=analysis.source_entity_alias,
                perspectives=analysis.perspectives,
                observations=analysis.observations,
                trait_profile=current_trait_profile.model_copy(deep=True),
                trait_evidence=[], trait_changes=[], batch_id=batch_id,
                aspect_updates=[], goal_updates=[],
                subtitle_change=SubtitleChangeProposal(),
                llm_calls=list(self.llm_calls),
            )

        if on_stage:
            await on_stage(
                "source:{0} - Step 3: Deterministic source reduction".format(
                    analysis.source_entity_alias
            ), [3]
            )
        existing = current_trait_evidence or []
        incoming = ground_observations(analysis.observations.trait_evidence,
            scene_ids=[p.scene_id for p in analysis.perspectives],
            source_group_id=analysis.source_entity_id,
            offset=max((e.chronological_position for e in existing), default=-1) + 1)
        accumulated = merge_evidence(existing, incoming)
        profile_result = _deterministic_profile_result(
            evidence=accumulated,
            source_entity_id=analysis.source_entity_id,
            aspect_signals=analysis.aspect_signals,
            goal_signals=analysis.goal_signals,
            current_aspects=current_aspects,
            current_goals=current_goals,
        )
        trait_profile, trait_changes = update_profile(
            current_trait_profile,
            accumulated,
            profile_result.trait_proposals,
            source_group_id=analysis.source_entity_id,
        )

        return EmbodyAgentResult(
            scene_input_digests=analysis.scene_input_digests,
            source_entity_id=analysis.source_entity_id,
            source_entity_alias=analysis.source_entity_alias,
            perspectives=analysis.perspectives,
            observations=analysis.observations,
            trait_profile=trait_profile,
            trait_evidence=incoming,
            trait_changes=trait_changes,
            batch_id=batch_id,
            aspect_updates=profile_result.aspect_updates,
            goal_updates=profile_result.goal_updates,
            subtitle_change=analysis.subtitle_change,
            llm_calls=list(self.llm_calls),
        )

    async def run(
        self,
        *,
        source_entity_id: str,
        source_entity_alias: str,
        canonical_identity: dict[str, Any],
        current_trait_profile: TraitProfile,
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        scenes: list[SceneInput],
        current_trait_evidence: list[TraitEvidence] | None = None,
        batch_id: str | None = None,
        on_stage: Any = None,
    ) -> EmbodyAgentResult:
        """Compatibility entry point for one source executed end to end."""

        analysis = await self.analyze(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            canonical_identity=canonical_identity,
            current_trait_profile=current_trait_profile,
            current_aspects=current_aspects,
            current_goals=current_goals,
            scenes=scenes,
            on_stage=on_stage,
        )
        return await self.apply_profile_update(
            analysis=analysis,
            current_trait_evidence=current_trait_evidence,
            batch_id=batch_id,
            current_trait_profile=current_trait_profile,
            current_aspects=current_aspects,
            current_goals=current_goals,
            on_stage=on_stage,
        )


def _model_output_schema(
    schema: type[BaseModel], output_binding: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the contract the model actually writes, not the persistence shape.

    Impacts select a supplied profile target by one-based ``target_index``.  The
    backend resolves that transient index to the persisted ``target_id`` before
    Pydantic validates the persistence schema.  Reusing the persistence schema
    here made native structured output contradict the prompt.
    """
    result = copy.deepcopy(schema.model_json_schema())
    if schema is not SceneEnrichmentsOutput:
        return result
    definitions = result.get("$defs", {})
    impact = definitions.get("CharacterImpactOutput")
    enrichment = definitions.get("SceneEnrichmentOutput")
    if not isinstance(impact, dict) or not isinstance(enrichment, dict):
        return result
    properties = impact.get("properties")
    required = impact.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return result
    properties.pop("target_id", None)
    properties["target_index"] = {"type": "integer", "minimum": 1}
    impact["required"] = [field for field in required if field != "target_id"] + ["target_index"]
    targets = (output_binding or {}).get("profile_targets") or {}
    target_count = max(len(targets.get("aspects", [])), len(targets.get("goals", [])))
    impacts = enrichment.get("properties", {}).get("impacts")
    if isinstance(impacts, dict):
        if target_count == 0:
            impacts["maxItems"] = 0
        else:
            properties["target_index"]["maximum"] = target_count
    return result


def _normalize_position_bound_collection(
    parsed: Any, output_binding: dict[str, Any] | None,
) -> Any:
    """Accept an unambiguous missing outer collection wrapper.

    Some providers return the sole item instead of the documented container for a
    one-scene request.  The backend already owns position binding, so wrapping it
    is lossless.  A bare object is never inferred for multi-scene work; that
    remains a schema error and follows the bounded correction/recovery path.
    """
    if not output_binding:
        return parsed
    collection = output_binding.get("collection")
    scene_ids = output_binding.get("scene_ids")
    if not isinstance(collection, str) or not isinstance(scene_ids, list):
        return parsed
    if isinstance(parsed, list):
        logger.info(
            "embodiment_output_wrapper_normalized collection=%s item_count=%d shape=list",
            collection, len(parsed),
        )
        return {collection: parsed}
    if (
        len(scene_ids) == 1
        and isinstance(parsed, dict)
        and collection not in parsed
    ):
        logger.info(
            "embodiment_output_wrapper_normalized collection=%s item_count=1 shape=object",
            collection,
        )
        return {collection: [parsed]}
    return parsed


def _bind_model_output_references(
    value: Any, *, collection: str | None = None, scene_ids: list[str] | None = None,
    evidence_id: str | None = None, profile_targets: dict[str, list[str]] | None = None,
    observation_ids: list[str] | None = None, evidence_ids: list[str] | None = None,
) -> None:
    """Attach backend-owned identifiers to position-bound model output."""
    if not isinstance(value, dict):
        return
    if collection and scene_ids is not None:
        records = value.get(collection)
        if isinstance(records, list):
            for index, record in enumerate(records):
                scene_id = (
                    scene_ids[index] if index < len(scene_ids)
                    else f"__extra_position_{index + 1}"
                )
                if isinstance(record, dict):
                    _bind_scene_local_references(record, scene_id, profile_targets)
    if evidence_id is not None:
        _bind_authored_evidence_references(value, evidence_id)

    if observation_ids is not None:
        for proposal in value.get("trait_proposals", []):
            if not isinstance(proposal, dict):
                continue
            indexes = proposal.pop("observation_indexes", None)
            if isinstance(indexes, list) and all(isinstance(index, int) for index in indexes):
                proposal["observation_ids"] = [
                    observation_ids[index - 1] for index in indexes
                    if 1 <= index <= len(observation_ids)
                ]
    if evidence_ids is not None:
        for key in ("aspect_updates", "goal_updates"):
            for update in value.get(key, []):
                if not isinstance(update, dict):
                    continue
                indexes = update.pop("evidence_indexes", None)
                if isinstance(indexes, list) and all(isinstance(index, int) for index in indexes):
                    update["evidence_ids"] = [
                        evidence_ids[index - 1] for index in indexes
                        if 1 <= index <= len(evidence_ids)
                    ]


def _bind_scene_local_references(
    item: dict[str, Any], scene_id: str, profile_targets: dict[str, list[str]] | None,
) -> None:
    item["scene_id"] = scene_id
    canonical_evidence_id = f"scene:{scene_id}"
    item["evidence_ids"] = [canonical_evidence_id]
    _bind_evidence_references(item, canonical_evidence_id, scene_id)
    for key in ("trait_candidates", "aspect_signals", "goal_signals"):
        for nested in item.get(key, []):
            if isinstance(nested, dict):
                nested["evidence_ids"] = [canonical_evidence_id]
                if key == "trait_candidates":
                    nested["episode_id"] = canonical_evidence_id
                    nested["available_after_scene_id"] = scene_id

    if "impacts" in item:
        resolved_impacts = []
        for impact in item["impacts"]:
            if not isinstance(impact, dict) or profile_targets is None:
                continue
            target_index = impact.pop("target_index", None)
            impact.pop("target_id", None)
            target_kind = "goals" if impact.get("impact_type") == "goal_change" else "aspects"
            targets = profile_targets.get(target_kind, [])
            if not isinstance(target_index, int) or not 1 <= target_index <= len(targets):
                logger.info(
                    "embodiment_unresolvable_impact_dropped scene_id=%s impact_type=%s target_index=%s",
                    scene_id, impact.get("impact_type"), target_index,
                )
                continue
            impact["target_id"] = targets[target_index - 1]
            resolved_impacts.append(impact)
        item["impacts"] = resolved_impacts


def _bind_authored_evidence_references(value: dict[str, Any], evidence_id: str) -> None:
    """Attach the one canonical identity citation to baseline observations."""
    for observation in value.get("trait_evidence", []):
        if isinstance(observation, dict):
            observation["evidence_ids"] = [evidence_id]
            observation["episode_id"] = evidence_id
            observation["available_after_scene_id"] = None
    _bind_evidence_references(value, evidence_id)


def _bind_evidence_references(value: Any, evidence_id: str, scene_id: str | None = None) -> None:
    if isinstance(value, list):
        for item in value:
            _bind_evidence_references(item, evidence_id, scene_id)
        return
    if not isinstance(value, dict):
        return
    if "evidence_ids" in value:
        value["evidence_ids"] = [evidence_id]
    if "evidence_id" in value:
        value["evidence_id"] = evidence_id
    if "episode_id" in value:
        value["episode_id"] = evidence_id
    if scene_id is not None and "available_after_scene_id" in value:
        value["available_after_scene_id"] = scene_id
    for nested in value.values():
        _bind_evidence_references(nested, evidence_id, scene_id)


def _collect_evidence_ids(data: Any) -> set[str]:
    """Recursively collect all evidence_id and evidence_ids values from nested dicts/lists."""
    ids: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "evidence_id" and isinstance(value, str):
                ids.add(value)
            if key == "evidence_ids" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        ids.add(item)
            ids.update(_collect_evidence_ids(value))
    elif isinstance(data, list):
        for item in data:
            ids.update(_collect_evidence_ids(item))
    return ids


_OBSERVATION_EVIDENCE_LISTS = {
    "recurring_behaviours", "motivations", "values", "fears", "conflicts",
    "relationships", "contradictions", "evidence_gaps",
}
_PROFILE_EVIDENCE_LISTS = {
    "aspect_updates", "goal_updates",
}


def _drop_ungrounded_output_items(
    parsed: Any, *, schema: type[BaseModel],
) -> int:
    """Drop only output-list entries that cannot cite any evidence.

    Unknown non-empty references remain present and are rejected by semantic
    validation. This normalization handles no-op placeholders such as
    ``{"text": "No contradictions", "evidence_ids": []}``.
    """
    if not isinstance(parsed, dict):
        return 0
    fields: set[str]
    if schema is EmbodimentObservationsOutput:
        fields = _OBSERVATION_EVIDENCE_LISTS
    elif schema is ProfileUpdateOutput:
        fields = _PROFILE_EVIDENCE_LISTS
    else:
        return 0

    dropped = 0
    for field in fields:
        items = parsed.get(field)
        if not isinstance(items, list):
            continue
        grounded: list[Any] = []
        for item in items:
            evidence_ids = item.get("evidence_ids") if isinstance(item, dict) else None
            if not isinstance(evidence_ids, list) or not evidence_ids:
                dropped += 1
                continue
            grounded.append(item)
        parsed[field] = grounded

    if schema is EmbodimentObservationsOutput:
        subtitle = parsed.get("subtitle_change")
        if (
            isinstance(subtitle, dict)
            and subtitle.get("operation") in {"set", "clear"}
            and not subtitle.get("evidence_ids")
        ):
            parsed.pop("subtitle_change", None)
            dropped += 1
    return dropped


def _canonical_evidence_id(value: str) -> str:
    normalized = value.strip()
    return normalized if ":" in normalized else f"scene:{normalized}"


def _normalize_evidence_ids(data: Any) -> None:
    """Canonicalize evidence references in a validated Pydantic object in place."""
    if isinstance(data, BaseModel):
        for field_name in type(data).model_fields:
            value = getattr(data, field_name)
            if field_name == "evidence_id" and isinstance(value, str):
                setattr(data, field_name, _canonical_evidence_id(value))
            elif field_name == "evidence_ids" and isinstance(value, list):
                setattr(
                    data, field_name,
                    [_canonical_evidence_id(item) for item in value],
                )
            elif field_name == "available_after_scene_id" and isinstance(value, str):
                # Evidence references are canonicalized with ``scene:``, but
                # this cutoff deliberately stores the raw input scene ID.  LLMs
                # commonly mirror the evidence-reference form; it is an
                # unambiguous equivalent, so normalize it before grounding.
                setattr(data, field_name, value.removeprefix("scene:").strip())
            else:
                _normalize_evidence_ids(value)
    elif isinstance(data, list):
        for item in data:
            _normalize_evidence_ids(item)
    elif isinstance(data, dict):
        for key, value in data.items():
            if key == "evidence_id" and isinstance(value, str):
                data[key] = _canonical_evidence_id(value)
            elif key == "evidence_ids" and isinstance(value, list):
                data[key] = [_canonical_evidence_id(item) for item in value]
            elif key == "available_after_scene_id" and isinstance(value, str):
                data[key] = value.removeprefix("scene:").strip()
            else:
                _normalize_evidence_ids(value)


def _validate_and_normalize_scene_grounding(items: list[Any], scene_ids: list[str]) -> None:
    """Canonicalize and require each scene-local result to cite only itself."""
    _normalize_evidence_ids(items)
    actual_scene_ids = [item.scene_id for item in items]
    if actual_scene_ids != scene_ids or len(actual_scene_ids) != len(set(actual_scene_ids)):
        raise EmbodimentGenerationError(
            "scene output positions must match the exact unique input order",
            category="semantic_reference",
            offending_ids=set(actual_scene_ids) - set(scene_ids),
            allowed_ids=set(scene_ids),
            expected_sequence=scene_ids,
            actual_sequence=actual_scene_ids,
        )
    validate_scene_grounding(items, scene_ids)
    for item in items:
        referenced = _collect_evidence_ids(item.model_dump(mode="json"))
        expected = {f"scene:{item.scene_id}"}
        if referenced != expected:
            raise EmbodimentGenerationError(
                "scene-local output must cite only its own scene evidence",
                category="semantic_reference",
                offending_ids=referenced - expected,
                allowed_ids=expected,
            )


def _validate_and_normalize_evidence(
    value: BaseModel, *, allowed_ids: set[str], stage: str,
) -> None:
    _normalize_evidence_ids(value)
    referenced = _collect_evidence_ids(value.model_dump(mode="json"))
    offending = referenced - allowed_ids
    if offending:
        raise EmbodimentGenerationError(
            f"{stage} referenced unknown evidence",
            category="semantic_reference",
            stage=stage,
            offending_ids=offending,
            allowed_ids=allowed_ids,
        )


def _semantic(action):
    try:
        return action()
    except ValueError as exc:
        raise EmbodimentGenerationError(str(exc), category="semantic_reference") from exc


def _validate_profile_update(value, *, allowed_ids: set[str], evidence: list[TraitEvidence]) -> None:
    for item in [*value.aspect_updates, *value.goal_updates]:
        _validate_and_normalize_evidence(item, allowed_ids=allowed_ids, stage="profile updates")
    _semantic(lambda: validate_proposals(value.trait_proposals, evidence))


def _normalized_identity_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _deterministic_profile_result(*, evidence, source_entity_id: str, aspect_signals, goal_signals, current_aspects, current_goals) -> ProfileUpdateOutput:
    """Convert independently extracted source signals into safe source operations.

    Numeric trait updates are already backend-owned.  Durable signal additions
    are deduplicated against active state and each other; no model may mutate or
    remove existing identity state in this reduction.
    """
    proposals = []
    for trait in sorted({item.trait for item in evidence if item.eligible and item.source_group_id == source_entity_id}):
        ids = [item.id for item in evidence if item.trait == trait and item.eligible and item.source_group_id == source_entity_id]
        proposals.append({"trait": trait, "observation_ids": ids, "justification": "Validated source-local behavioral evidence.", "addresses_contradictions": "The deterministic policy retains contradictory evidence without averaging it into a false certainty."})
    known_aspects = {_normalized_identity_label(str(item.get("name") or "")) for item in current_aspects}
    known_goals = {_normalized_identity_label(str(item.get("title") or "")) for item in current_goals}
    aspect_updates = []
    for signal in sorted(aspect_signals, key=lambda item: (-item.confidence, -item.importance, item.name.casefold())):
        key = _normalized_identity_label(signal.name)
        if key in known_aspects:
            continue
        known_aspects.add(key)
        aspect_updates.append({"operation": "add", **signal.model_dump(mode="json")})
    goal_updates = []
    for signal in sorted(goal_signals, key=lambda item: (-item.confidence, -item.priority, item.title.casefold())):
        key = _normalized_identity_label(signal.title)
        if key in known_goals:
            continue
        known_goals.add(key)
        goal_updates.append({"operation": "add", **signal.model_dump(mode="json")})
    return ProfileUpdateOutput.model_validate({
        "trait_proposals": proposals,
        "aspect_updates": aspect_updates[:2],
        "goal_updates": goal_updates[:1],
    })
