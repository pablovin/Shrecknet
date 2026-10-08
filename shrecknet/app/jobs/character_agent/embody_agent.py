"""Scene-centric source-boundary CharacterAgent embodiment generation.

The persistent identity description is loaded or generated once before scene
work and refreshed once after all source bundles. Three LLM calls normally run
for each source scene chunk:
  1. Character incorporation grounded by the identity description
  2. Psychological enrichment and trait interpretation in parallel, both
     receiving canonical scenes and the Stage 1 source_type/perspective;
     each also receives its stage-specific identity/profile context

The backend then validates, grounds, and reduces extracted candidates
deterministically. A source's chunks share one starting identity; source bundles
run sequentially and each produces one revision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.integrations.llm.json_repair import repair_json_text
from app.integrations.llm.structured_output import (
    strict_json_schema,
    structured_output_is_unsupported,
)
from app.integrations.llm.shreckllm_client import LLMProviderUnavailableError
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.profile import _stable_profile_id
from app.jobs.character_agent.embody_agent_prompts import (
    IDENTITY_DESCRIPTION_PROMPT,
    PSYCHOLOGICAL_ANALYSIS_PROMPT,
    PSYCHOLOGICAL_CONSOLIDATION_PROMPT,
    PERSPECTIVE_PROMPT,
    PERSPECTIVE_TRUNCATION_RECOVERY_PROMPT,
    TRAIT_INTERPRETATION_PROMPT,
)
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    CharacterAspectCategory,
    CharacterGoalType,
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    EmbodyAgentResult,
    LLMCallRecord,
    AspectUpdateData,
    GoalUpdateData,
    SceneInput,
    SceneEnrichmentsOutput,
    ScenePerspectiveBundleOutput,
    ScenePerspectiveOutput,
    ScenePerspectiveSourceType,
    SubtitleChangeProposal,
    IdentityDescription,
    IdentityPersonalityTrait,
)


from app.schemas.character_traits import TraitProfile, TraitEvidence, DIRECTIONAL_TRAITS
from app.schemas.character_traits import TraitKey, TRAIT_BY_KEY
from app.services.character_trait_service import (
    ground_observations, merge_evidence, update_profile, validate_scene_grounding, scene_digest,
)

logger = logging.getLogger(__name__)

# Source scenes are analyzed in chunks of at most five, then combined before
# one source-level reduction and revision. Bound each provider response explicitly.
EMBODIMENT_LLM_MAX_TOKENS = 10_000


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
    """Persistent, backend-bound perspective records used after incorporation."""
    perspectives: list[ScenePerspectiveOutput]


class _IdentityDescriptionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_summary: str = Field(min_length=1)
    psychological_summary: str = Field(min_length=1)
    personality_traits: list[IdentityPersonalityTrait]


class ScenePerspectiveLLMOutput(BaseModel):
    """Compact model-owned fields for one position-bound scene perspective."""

    model_config = ConfigDict(extra="forbid")

    source_type: ScenePerspectiveSourceType
    perspective: str = Field(min_length=1, max_length=900)


class _PerspectivesLLMContainer(BaseModel):
    """LLM-facing incorporation contract; canonical references are backend-owned."""

    model_config = ConfigDict(extra="forbid")
    perspectives: list[ScenePerspectiveLLMOutput]


class _EmotionLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arousal: int = Field(ge=0, le=100)
    valence: int = Field(ge=0, le=100)
    description: str = Field(min_length=1, max_length=240)


class _BeliefLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=300)
    confidence: int = Field(ge=0, le=100)


class _ProfileEventLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["aspect", "goal"]
    description: str = Field(min_length=1, max_length=300)


class _TraitCandidateLLMOutput(BaseModel):
    """Model-owned trait observation fields; provenance is attached by the backend."""

    model_config = ConfigDict(extra="forbid")
    trait: TraitKey
    situation_type: str = Field(min_length=1, max_length=80)
    polarity: Literal["low", "high"]
    justification: str = Field(min_length=1, max_length=360)

    @model_validator(mode="after")
    def valid_context(self):
        if self.situation_type == "unspecified":
            return self
        parts = self.situation_type.split(":")
        if (len(parts) != 3
            or parts[0] not in TRAIT_BY_KEY[self.trait].diagnostic_situations
            or parts[1] not in {"friend", "enemy", "other"}
            or parts[2] not in {"ordinary", "high_stakes"}):
            raise ValueError("invalid trait situation context")
        return self


class _SceneEnrichmentLLMOutput(BaseModel):
    """Bounded psychological-analysis content without persistence references."""

    model_config = ConfigDict(extra="forbid")
    emotions: list[_EmotionLLMOutput] = Field(max_length=2)
    beliefs: list[_BeliefLLMOutput] = Field(max_length=2)
    profile_events: list[_ProfileEventLLMOutput] = Field(max_length=2)


class _ConsolidationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["add", "update", "status", "reinforce"]
    target_id: str | None = None
    candidate_id: str | None = None
    name: str | None = Field(None, max_length=255)
    title: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=1000)
    category: CharacterAspectCategory | None = None
    goal_type: CharacterGoalType | None = None
    status: Literal["active", "inactive", "completed", "abandoned", "superseded"] | None = None
    justification: str = Field(min_length=1, max_length=500)
    event_references: list[int] = Field(min_length=1)


class _PsychologicalConsolidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aspect_operations: list[_ConsolidationOperation]
    goal_operations: list[_ConsolidationOperation]
    focused_aspects: list[str] = Field(max_length=10)
    focused_goals: list[str] = Field(max_length=10)


class _ConsolidationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consolidation: _PsychologicalConsolidationOutput


class _SceneEnrichmentsLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_enrichments: list[_SceneEnrichmentLLMOutput]


class _SceneTraitInterpretationLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trait_candidates: list[_TraitCandidateLLMOutput] = Field(max_length=8)


class _SceneTraitInterpretationsLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_trait_interpretations: list[_SceneTraitInterpretationLLMOutput]


class UsageTracker:
    def __init__(self, llm_client):
        self.llm = llm_client
        self.calls: list[LLMCallRecord] = []
        self.stage_elapsed_seconds: dict[str, float] = {}
        self.last_response_metadata: dict[str, Any] = {}
        self.last_elapsed_seconds: float | None = None

    async def chat(self, *, stage: str, usage_tag: str, **kwargs) -> str:
        input_text = json.dumps(kwargs.get("messages", []), ensure_ascii=False)
        input_chars = len(input_text)
        input_tokens_est = max(1, input_chars // 4)

        started_at = time.monotonic()
        result = await self.llm.chat(
            usage_tag=usage_tag,
            return_metadata=True,
            **kwargs,
        )
        elapsed_seconds = time.monotonic() - started_at
        self.last_elapsed_seconds = elapsed_seconds
        self.stage_elapsed_seconds[stage] = (
            self.stage_elapsed_seconds.get(stage, 0.0) + elapsed_seconds
        )

        if isinstance(result, dict) and "text" in result:
            self.last_response_metadata = dict(result.get("response_metadata") or {})
            result = str(result["text"])
        else:
            # Lightweight test doubles may only return text. Production callers
            # always request metadata above, while this fallback retains their
            # existing contract.
            self.last_response_metadata = dict(
                getattr(self.llm, "last_response_metadata", {}) or {}
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
        scene_interpretation_model,
        semantic_correction_attempts: int = 1,
        debug_artifacts: EmbodimentDebugArtifacts | None = None,
        debug_source_index: int | None = None,
        debug_source_alias: str | None = None,
    ):
        self._llm = UsageTracker(llm_client)
        self.character_incorporation_model = character_incorporation_model
        self.scene_interpretation_model = scene_interpretation_model
        self._identity_description = None
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
                dict(self._llm.last_response_metadata),
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
            if output_binding and output_binding.get("bind_references", True):
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
            provider_reason = str(exc)
            category = (
                "provider_timeout"
                if "watchdog exceeded" in provider_reason.lower()
                else "transport"
            )
            raise EmbodimentGenerationError(
                f"{stage} transport failed",
                category=category,
                stage=stage,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                retryable=True,
                model_name=str(getattr(model, "name", model)),
                provider_reason=provider_reason,
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
        response_metadata = self._llm.last_response_metadata
        if response_metadata.get("finish_reason") == "length":
            scene_ids = (output_binding or {}).get("scene_ids")
            if stage == "character incorporation":
                logger.warning(
                    "character_incorporation_truncated source_id=%s source_alias=%s "
                    "scene_count=%s provider=%s requested_model=%s resolved_model=%s "
                    "reasoning_enabled=%s requested_max_tokens=%s prompt_tokens=%s "
                    "completion_tokens=%s finish_reason=%s response_chars=%s duration_ms=%s",
                    source_entity_id, source_entity_alias,
                    len(scene_ids) if isinstance(scene_ids, list) else None,
                    getattr(model, "provider", None), getattr(model, "name", model),
                    response_metadata.get("resolved_model"), response_metadata.get("reasoning_enabled"),
                    max_tokens, response_metadata.get("prompt_tokens"),
                    response_metadata.get("completion_tokens"), response_metadata.get("finish_reason"),
                    len(str(raw)), round((self._llm.last_elapsed_seconds or 0) * 1000, 2),
                )
            self._debug_call(
                stage=stage, prompt=prompt, payload=payload, raw_output=raw,
                error=(
                    "provider stopped the response at the configured output "
                    f"limit of {max_tokens} tokens"
                ),
                model=model, usage_tag=usage_tag,
                requested_max_tokens=max_tokens,
                scene_count=len(scene_ids) if isinstance(scene_ids, list) else None,
            )
            raise EmbodimentGenerationError(
                f"{stage} response reached its {max_tokens}-token output limit",
                category="truncated",
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
            response_metadata = self._llm.last_response_metadata
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
                        max_tokens=max_tokens,
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

    async def generate_identity_description(
        self, *, canonical_identity: dict[str, Any], current_profile: TraitProfile,
        current_aspects: list[dict[str, Any]], current_goals: list[dict[str, Any]],
    ) -> IdentityDescription:
        """Synthesize the persistent narrative identity once per embodiment phase."""
        payload = {
            "name": canonical_identity.get("alias") or "Character",
            "entity_type": canonical_identity.get("entity_type") or "Unknown",
            "entity_descriptor": canonical_identity.get("entity_type_description"),
            "autogenerated_text": canonical_identity.get("generated_text"),
            "text": canonical_identity.get("authored_text"),
            "properties": [
                {"name": str(name), "value": value}
                for name, value in (canonical_identity.get("properties") or {}).items()
            ],
            "goals": [{"name": item.get("title") or item.get("name"),
                       "description": item.get("description")}
                      for item in current_goals],
            "aspects": [{"name": item.get("name"), "type": item.get("category"),
                         "descriptor": item.get("description")}
                        for item in current_aspects],
            "traits": [{"name": key, "value": current_profile.estimate(key).point}
                       for key in DIRECTIONAL_TRAITS],
        }
        output = await self._call(
            prompt=IDENTITY_DESCRIPTION_PROMPT, payload=payload,
            schema=_IdentityDescriptionOutput, stage="identity description",
            usage_tag="character_agent.embodiment.identity_description",
            max_tokens=EMBODIMENT_LLM_MAX_TOKENS,
            model=self.scene_interpretation_model,
        )
        return IdentityDescription.model_validate(output.model_dump(mode="json"))

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
        """Run the perspective stage for one source scene chunk."""
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")
        expected_ids = [scene.scene_id for scene in scenes]
        return await self._incorporate_perspectives(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            identity={key: canonical_identity.get(key) for key in (
                "alias", "subtitle", "entity_type", "entity_type_description", "properties",
                "identity_description",
            )},
            scene_list=[
                {"position": index + 1, "name": scene.name,
                 "description": scene.description, "created_at": scene.created_at}
                for index, scene in enumerate(scenes)
            ],
            expected_ids=expected_ids,
        )

    async def _incorporate_perspectives(
        self, *, source_entity_id: str, source_entity_alias: str,
        identity: dict[str, Any], scene_list: list[dict[str, Any]],
        expected_ids: list[str],
    ) -> _PerspectivesContainer:
        """Generate compact LLM-owned fields, then bind canonical scene references."""
        payload = {"identity": identity, "scenes": scene_list}
        output_binding = {
            "collection": "perspectives",
            "scene_ids": expected_ids,
            "bind_references": False,
        }
        call_args = {
            "payload": payload,
            "schema": _PerspectivesLLMContainer,
            "stage": "character incorporation",
            "max_tokens": EMBODIMENT_LLM_MAX_TOKENS,
            "model": self.character_incorporation_model,
            "source_entity_id": source_entity_id,
            "source_entity_alias": source_entity_alias,
            "schema_correction_attempts": 1,
            "output_binding": output_binding,
        }
        try:
            result = await self._call(
                prompt=PERSPECTIVE_PROMPT,
                usage_tag="character_agent.embodiment.character_incorporation",
                **call_args,
            )
        except EmbodimentGenerationError as exc:
            if exc.category != "truncated":
                raise
            logger.warning(
                "character_incorporation_truncation_recovery source_id=%s "
                "source_alias=%s scene_count=%d",
                source_entity_id, source_entity_alias, len(expected_ids),
            )
            result = await self._call(
                prompt=PERSPECTIVE_TRUNCATION_RECOVERY_PROMPT,
                usage_tag="character_agent.embodiment.character_incorporation.truncation_recovery",
                **call_args,
            )

        perspectives = _bind_llm_perspectives(result, expected_ids)
        _semantic(lambda: _validate_and_normalize_scene_grounding(
            perspectives.perspectives, expected_ids,
        ))
        self._log_character_incorporation_completed(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            perspectives=perspectives.perspectives,
        )
        return perspectives

    def _log_character_incorporation_completed(
        self, *, source_entity_id: str, source_entity_alias: str,
        perspectives: list[ScenePerspectiveOutput],
    ) -> None:
        metadata = self._llm.last_response_metadata
        perspective_lengths = [len(item.perspective) for item in perspectives]
        logger.info(
            "character_incorporation_completed source_id=%s source_alias=%s "
            "scene_count=%d perspective_count=%d provider=%s requested_model=%s "
            "resolved_model=%s reasoning_enabled=%s requested_max_tokens=%d "
            "prompt_tokens=%s completion_tokens=%s finish_reason=%s response_chars=%s "
            "duration_ms=%s perspective_chars=%s",
            source_entity_id, source_entity_alias, len(perspectives), len(perspectives),
            getattr(self.character_incorporation_model, "provider", None),
            getattr(self.character_incorporation_model, "name", self.character_incorporation_model),
            metadata.get("resolved_model"), metadata.get("reasoning_enabled"),
            EMBODIMENT_LLM_MAX_TOKENS, metadata.get("prompt_tokens"),
            metadata.get("completion_tokens"), metadata.get("finish_reason"),
            self._llm.calls[-1].output_chars if self._llm.calls else None,
            round((self._llm.last_elapsed_seconds or 0) * 1000, 2),
            (min(perspective_lengths), max(perspective_lengths),
             round(sum(perspective_lengths) / len(perspective_lengths), 1)),
        )

    async def _analyze_psychological_batch(
        self, *, source_entity_id: str, source_entity_alias: str,
        perspectives: list[ScenePerspectiveOutput], aspects: list[dict[str, Any]],
        goals: list[dict[str, Any]], scene_contexts: list[dict[str, Any]],
        identity_description: Any = None, recovery_depth: int = 0,
    ) -> list[Any]:
        """Analyze one perspective batch, splitting only a truncated retry batch."""
        expected_ids = [perspective.scene_id for perspective in perspectives]
        profile_targets = {
            "aspects": [aspect["id"] for aspect in aspects],
            "goals": [goal["id"] for goal in goals],
        }
        payload = {
            "identity_description": identity_description,
            "scenes": [
                {
                    **scene_contexts[index],
                    "agent_scene_interpretation": {
                        "source_type": perspective.source_type.value,
                        "perspective": perspective.perspective,
                    },
                }
                for index, perspective in enumerate(perspectives)
            ],
        }
        usage_tag = "character_agent.embodiment.scene_interpretation"
        if recovery_depth:
            usage_tag += ".truncation_recovery.{0}".format(recovery_depth)
        try:
            llm_result = await self._call(
                prompt=PSYCHOLOGICAL_ANALYSIS_PROMPT, payload=payload,
                schema=_SceneEnrichmentsLLMOutput, stage="scene trait extraction",
                usage_tag=usage_tag, max_tokens=EMBODIMENT_LLM_MAX_TOKENS,
                model=self.scene_interpretation_model, source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias, schema_correction_attempts=1,
                output_binding={
                    "collection": "scene_enrichments", "scene_ids": expected_ids,
                    "bind_references": False,
                },
            )
        except EmbodimentGenerationError as exc:
            if exc.category != "truncated" or len(perspectives) == 1:
                raise
            midpoint = len(perspectives) // 2
            logger.warning(
                "psychological_analysis_truncation_recovery source_id=%s source_alias=%s "
                "scene_count=%d retry_batch_sizes=%s",
                source_entity_id, source_entity_alias, len(perspectives),
                (midpoint, len(perspectives) - midpoint),
            )
            first = await self._analyze_psychological_batch(
                source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                perspectives=perspectives[:midpoint], aspects=aspects, goals=goals,
                scene_contexts=scene_contexts[:midpoint],
                identity_description=identity_description,
                recovery_depth=recovery_depth + 1,
            )
            second = await self._analyze_psychological_batch(
                source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                perspectives=perspectives[midpoint:], aspects=aspects, goals=goals,
                scene_contexts=scene_contexts[midpoint:],
                identity_description=identity_description,
                recovery_depth=recovery_depth + 1,
            )
            return [*first, *second]
        return _bind_llm_enrichments(llm_result, expected_ids, profile_targets).scene_enrichments

    async def _interpret_traits_batch(
        self, *, source_entity_id: str, source_entity_alias: str,
        identity: dict[str, Any], perspectives: list[ScenePerspectiveOutput],
        scene_contexts: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """Interpret trait signals from canonical scenes without prior personality context."""
        expected_ids = [item.scene_id for item in perspectives]
        identity_description = identity.get("identity_description") or {}
        payload = {
            "target": {
                "alias": identity.get("alias"),
                "identity_summary": identity_description.get("identity_summary"),
                "psychological_summary": identity_description.get("psychological_summary"),
            },
            "scenes": scene_contexts,
        }
        result = await self._call(
            prompt=TRAIT_INTERPRETATION_PROMPT, payload=payload,
            schema=_SceneTraitInterpretationsLLMOutput, stage="trait interpretation",
            usage_tag="character_agent.embodiment.trait_interpretation",
            max_tokens=EMBODIMENT_LLM_MAX_TOKENS, model=self.scene_interpretation_model,
            source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
            schema_correction_attempts=1,
            output_binding={"collection": "scene_trait_interpretations", "scene_ids": expected_ids,
                            "bind_references": False},
        )
        if len(result.scene_trait_interpretations) != len(perspectives):
            raise EmbodimentGenerationError(
                "trait interpretation must return exactly one result per scene", category="schema",
                expected_sequence=expected_ids,
                actual_sequence=[str(index + 1) for index in range(len(result.scene_trait_interpretations))],
            )
        candidates: list[list[dict[str, Any]]] = []
        for scene_id, perspective, interpretation in zip(
            expected_ids, perspectives, result.scene_trait_interpretations, strict=True,
        ):
            scene_candidates: list[dict[str, Any]] = []
            selected_candidates: dict[str, _TraitCandidateLLMOutput] = {}
            for candidate in interpretation.trait_candidates:
                if candidate.trait in selected_candidates:
                    raise EmbodimentGenerationError(
                        "duplicate trait candidate for one perspective", category="schema",
                    )
                selected_candidates[candidate.trait] = candidate
            for candidate in selected_candidates.values():
                value = candidate.model_dump(mode="json")
                value.update(evidence_kind="behavior", perspective_id=perspective.id,
                             evidence_ids=[f"scene:{scene_id}"])
                scene_candidates.append(value)
            candidates.append(scene_candidates)
        return candidates

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
        perspectives_result: _PerspectivesContainer | None = None,
    ) -> EmbodyAgentAnalysis:
        self._identity_description = canonical_identity.get("identity_description")
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")

        scene_list = [
            {"position": index + 1, "name": scene.name, "description": scene.description,
             "created_at": scene.created_at}
            for index, scene in enumerate(scenes)
        ]
        scene_contexts = [
            {"position": index + 1, "scene": {"name": scene.name, "description": scene.description}}
            for index, scene in enumerate(scenes)
        ]
        known = {f"scene:{s.scene_id}" for s in scenes}
        identity = {
            key: canonical_identity.get(key)
            for key in (
                "alias", "subtitle", "entity_type",
                "entity_type_description", "properties", "identity_description",
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
        else:
            perspectives_result = await self._incorporate_perspectives(
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                identity=identity,
                scene_list=scene_list,
                expected_ids=[s.scene_id for s in scenes],
            )
        perspectives = perspectives_result.perspectives
        expected_ids = [s.scene_id for s in scenes]
        actual_ids = [p.scene_id for p in perspectives]
        if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
            raise EmbodimentGenerationError(
                "perspective output scene_ids must match input scene order and be unique"
            )
        _semantic(lambda: _validate_and_normalize_scene_grounding(perspectives, expected_ids))
        # Stage 2 receives the grounded interpretation; Stage 3 receives canonical
        # scenes and trait definitions only to prevent personality feedback.
        if on_stage:
            await on_stage(
                "source:{0} - Steps 2-3: Psychological analysis and trait interpretation".format(source_entity_alias), [2, 3]
            )
        enrichments, trait_candidates = await asyncio.gather(
            self._analyze_psychological_batch(
                source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                perspectives=perspectives, aspects=aspects, goals=goals,
                scene_contexts=scene_contexts,
                identity_description=canonical_identity.get("identity_description"),
            ),
            self._interpret_traits_batch(
                source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                identity=canonical_identity,
                perspectives=perspectives, scene_contexts=scene_contexts,
            ),
        )
        enrichment_ids = [item.scene_id for item in enrichments]
        if enrichment_ids != expected_ids or len(enrichment_ids) != len(set(enrichment_ids)):
            raise EmbodimentGenerationError(
                "trait extraction output scene_ids must match input scene order and be unique"
            )
        _semantic(lambda: _validate_and_normalize_scene_grounding(enrichments, expected_ids))
        bundles = [
            ScenePerspectiveBundleOutput(
                **perspective.model_dump(mode="json"),
                emotions=enrichment.emotions,
                beliefs=enrichment.beliefs,
                profile_events=enrichment.profile_events,
                trait_candidates=trait_candidates[index],
            )
            for index, (perspective, enrichment) in enumerate(zip(perspectives, enrichments, strict=True))
        ]

        # The two second-wave branches already contain all source-local evidence.
        # Construct observations in the backend so no additional reconstruction
        # call can reintroduce raw scenes or an accumulated evidence history.
        observations = EmbodimentObservationsOutput(
            trait_evidence=[
                candidate
                for scene_candidates in trait_candidates
                for candidate in scene_candidates
            ],
        )
        _validate_and_normalize_evidence(
            observations, allowed_ids=known, stage="scene-local trait candidates",
        )
        _semantic(lambda: ground_observations(
            observations.trait_evidence,
            scene_ids=expected_ids,
            perspective_ids=[p.id for p in perspectives],
            source_group_id=source_entity_id,
        ))

        return EmbodyAgentAnalysis(
            scene_input_digests={s.scene_id: scene_digest(s.model_dump()) for s in scenes},
            source_entity_id=source_entity_id,
            source_entity_alias=str(source_entity_alias),
            identity_description=IdentityDescription.model_validate(canonical_identity["identity_description"])
                if canonical_identity.get("identity_description") else None,
            perspectives=bundles,
            observations=observations,
            subtitle_change=observations.subtitle_change or SubtitleChangeProposal(),
            evidence_ids=known,
            profile_events=[event for item in bundles for event in item.profile_events],
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
                focused_aspects=[str(x["id"]) for x in current_aspects if x.get("in_focus")][:10],
                focused_goals=[str(x["id"]) for x in current_goals if x.get("in_focus")][:10],
                subtitle_change=SubtitleChangeProposal(),
                llm_calls=list(self.llm_calls),
            )

        if on_stage:
            await on_stage("source:{0} - Step 3: deterministic trait aggregation".format(
                analysis.source_entity_alias), [3])
        existing = current_trait_evidence or []
        incoming = ground_observations(analysis.observations.trait_evidence,
            scene_ids=[p.scene_id for p in analysis.perspectives],
            perspective_ids=[p.id for p in analysis.perspectives],
            source_group_id=analysis.source_entity_id,
            offset=max((e.chronological_position for e in existing), default=-1) + 1)
        accumulated = merge_evidence(existing, incoming)
        profile_result = await self._consolidate_profile(
            analysis=analysis, current_aspects=current_aspects,
            current_goals=current_goals, on_stage=on_stage,
        )
        trait_profile, trait_changes = update_profile(
            current_trait_profile,
            accumulated,
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
            aspect_updates=profile_result["aspect_updates"],
            goal_updates=profile_result["goal_updates"],
            focused_aspects=profile_result["focused_aspects"],
            focused_goals=profile_result["focused_goals"],
            subtitle_change=analysis.subtitle_change,
            llm_calls=[*analysis.llm_calls, *self.llm_calls],
        )

    async def _consolidate_profile(
        self, *, analysis: EmbodyAgentAnalysis,
        current_aspects: list[dict[str, Any]], current_goals: list[dict[str, Any]],
        on_stage: Any = None,
    ) -> dict[str, Any]:
        events = list(analysis.profile_events)
        if not events:
            return {"aspect_updates": [], "goal_updates": [],
                    "focused_aspects": [str(x["id"]) for x in current_aspects if x.get("in_focus")][:10],
                    "focused_goals": [str(x["id"]) for x in current_goals if x.get("in_focus")][:10]}
        if on_stage:
            await on_stage("source:{0} - Stage 4: psychological consolidation".format(
                analysis.source_entity_alias), [4])
        payload = {
            "identity_description": analysis.identity_description.model_dump(mode="json")
                if analysis.identity_description else None,
            "events": [{"position": i, "kind": event.kind.value if hasattr(event.kind, "value") else event.kind,
                        "description": event.description, "scene_id": event.scene_id}
                       for i, event in enumerate(events, 1)],
            "aspects": [{key: item.get(key) for key in
                         ("id", "name", "description", "category", "status", "in_focus")}
                        for item in current_aspects],
            "goals": [{key: item.get(key) for key in
                       ("id", "title", "description", "goal_type", "status", "in_focus")}
                      for item in current_goals],
        }
        output = await self._call(
            prompt=PSYCHOLOGICAL_CONSOLIDATION_PROMPT, payload=payload,
            schema=_ConsolidationEnvelope, stage="psychological consolidation",
            usage_tag="character_agent.embodiment.psychological_consolidation",
            max_tokens=EMBODIMENT_LLM_MAX_TOKENS,
            model=self.scene_interpretation_model,
            source_entity_id=analysis.source_entity_id,
            source_entity_alias=analysis.source_entity_alias,
            schema_correction_attempts=1,
        )
        consolidation = output.consolidation
        aspect_ids = {str(item.get("id")) for item in current_aspects}
        goal_ids = {str(item.get("id")) for item in current_goals}
        candidate_ids: set[str] = set()

        def convert(items, *, kind: str):
            result = []
            known = aspect_ids if kind == "aspect" else goal_ids
            for op in items:
                if any(index < 1 or index > len(events) for index in op.event_references):
                    raise EmbodimentGenerationError("consolidation cited an unknown event", category="semantic_reference")
                expected_kind = "aspect" if kind == "aspect" else "goal"
                if any(events[index - 1].kind != expected_kind for index in op.event_references):
                    raise EmbodimentGenerationError("consolidation cited an event of the wrong kind", category="semantic_reference")
                if (kind == "aspect" and op.goal_type is not None) or (kind == "goal" and op.category is not None):
                    raise EmbodimentGenerationError("consolidation supplied a category for the wrong profile type", category="schema")
                if op.operation == "status" and op.status is None:
                    raise EmbodimentGenerationError("status operation requires a lifecycle status", category="schema")
                if op.operation in {"update", "reinforce"} and op.status is not None:
                    raise EmbodimentGenerationError("lifecycle changes require a status operation", category="schema")
                if op.operation == "add":
                    if op.target_id is not None:
                        raise EmbodimentGenerationError("consolidation add cannot target an existing ID", category="semantic_reference")
                    label = op.name if kind == "aspect" else op.title
                    if not op.candidate_id or not label:
                        raise EmbodimentGenerationError("consolidation add needs candidate_id and name", category="schema")
                    if kind == "aspect" and not label.strip().casefold().startswith("i "):
                        raise EmbodimentGenerationError("aspect name must be a first-person defining statement", category="schema")
                    stable = _stable_profile_id(kind, label)
                    if op.candidate_id != stable or stable in candidate_ids:
                        raise EmbodimentGenerationError("invalid or duplicate source candidate ID", category="semantic_reference")
                    candidate_ids.add(stable)
                elif op.target_id not in known and op.target_id not in candidate_ids:
                    raise EmbodimentGenerationError("consolidation referenced unknown profile ID", category="semantic_reference")
                elif op.candidate_id is not None:
                    raise EmbodimentGenerationError("existing-item operation cannot use candidate_id", category="semantic_reference")
                citations = sorted({f"scene:{events[i-1].scene_id}" for i in op.event_references if events[i-1].scene_id})
                common = {"operation": op.operation, "target_id": op.target_id,
                          "candidate_id": op.candidate_id, "justification": op.justification,
                          "evidence_ids": citations, "event_references": op.event_references}
                if kind == "aspect":
                    label = op.name or next((x["name"] for x in current_aspects if str(x.get("id")) == op.target_id), "")
                    result.append(AspectUpdateData.model_validate({**common, "name": label,
                        "category": op.category, "description": op.description, "status": op.status}))
                else:
                    label = op.title or next((x["title"] for x in current_goals if str(x.get("id")) == op.target_id), "")
                    result.append(GoalUpdateData.model_validate({**common, "title": label,
                        "goal_type": op.goal_type, "description": op.description, "status": op.status}))
            return result

        aspects = convert(consolidation.aspect_operations, kind="aspect")
        goals = convert(consolidation.goal_operations, kind="goal")
        all_ids = aspect_ids | goal_ids | candidate_ids
        focus_aspects, focus_goals = consolidation.focused_aspects, consolidation.focused_goals
        if len(set(focus_aspects)) != len(focus_aspects) or len(set(focus_goals)) != len(focus_goals):
            raise EmbodimentGenerationError("duplicate focus reference", category="semantic_reference")
        aspect_candidate_ids = {op.candidate_id for op in aspects if op.candidate_id}
        goal_candidate_ids = {op.candidate_id for op in goals if op.candidate_id}
        if not set(focus_aspects) <= aspect_ids | aspect_candidate_ids:
            raise EmbodimentGenerationError("aspect focus references a non-aspect", category="semantic_reference")
        if not set(focus_goals) <= goal_ids | goal_candidate_ids:
            raise EmbodimentGenerationError("goal focus references a non-goal", category="semantic_reference")
        if not set(focus_aspects + focus_goals) <= all_ids:
            raise EmbodimentGenerationError("focus references unknown profile ID", category="semantic_reference")
        aspect_state = {str(x.get("id")): getattr(x.get("status", "active"), "value", x.get("status", "active")) for x in current_aspects}
        goal_state = {str(x.get("id")): getattr(x.get("status", "active"), "value", x.get("status", "active")) for x in current_goals}
        for op in aspects:
            key = op.candidate_id if op.operation.value == "add" else op.target_id
            if key and (op.operation.value in {"add", "status"}):
                aspect_state[str(key)] = op.status.value if op.status else "active"
        for op in goals:
            key = op.candidate_id if op.operation.value == "add" else op.target_id
            if key and (op.operation.value in {"add", "status"}):
                goal_state[str(key)] = op.status.value if op.status else "active"
        if any(aspect_state.get(item) != "active" for item in focus_aspects):
            raise EmbodimentGenerationError("inactive aspect cannot be focused", category="semantic_reference")
        if any(goal_state.get(item) != "active" for item in focus_goals):
            raise EmbodimentGenerationError("resolved goal cannot be focused", category="semantic_reference")
        return {"aspect_updates": aspects, "goal_updates": goals,
                "focused_aspects": focus_aspects, "focused_goals": focus_goals}

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
        """Execute the current two-wave pipeline for one source."""
        perspectives = await self.generate_perspectives(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            canonical_identity=canonical_identity,
            current_trait_profile=current_trait_profile,
            current_aspects=current_aspects,
            current_goals=current_goals,
            scenes=scenes,
        )
        analysis = await self.analyze(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            canonical_identity=canonical_identity,
            current_trait_profile=current_trait_profile,
            current_aspects=current_aspects,
            current_goals=current_goals,
            scenes=scenes,
            on_stage=on_stage,
            perspectives_result=perspectives,
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
    """Return the model-owned schema with exact position-bound list length."""
    result = copy.deepcopy(schema.model_json_schema())
    if schema is _PerspectivesLLMContainer:
        scene_ids = (output_binding or {}).get("scene_ids")
        perspectives = result.get("properties", {}).get("perspectives")
        if isinstance(scene_ids, list) and isinstance(perspectives, dict):
            perspectives["minItems"] = len(scene_ids)
            perspectives["maxItems"] = len(scene_ids)
        return result

    if schema is _SceneTraitInterpretationsLLMOutput:
        interpretations = result.get("properties", {}).get("scene_trait_interpretations")
        scene_ids = (output_binding or {}).get("scene_ids")
        if isinstance(scene_ids, list) and isinstance(interpretations, dict):
            interpretations["minItems"] = len(scene_ids)
            interpretations["maxItems"] = len(scene_ids)
        return result

    if schema is not _SceneEnrichmentsLLMOutput:
        return result
    enrichments = result.get("properties", {}).get("scene_enrichments")
    scene_ids = (output_binding or {}).get("scene_ids")
    if isinstance(scene_ids, list) and isinstance(enrichments, dict):
        enrichments["minItems"] = len(scene_ids)
        enrichments["maxItems"] = len(scene_ids)
    return result


def _bind_llm_perspectives(
    value: BaseModel, scene_ids: list[str],
) -> _PerspectivesContainer:
    """Materialize backend-owned scene references after LLM-only validation."""
    if not isinstance(value, _PerspectivesLLMContainer):
        raise EmbodimentGenerationError("invalid character incorporation result")
    if len(value.perspectives) != len(scene_ids):
        raise EmbodimentGenerationError(
            "character incorporation must return exactly one perspective per scene",
            category="schema",
            expected_sequence=scene_ids,
            actual_sequence=[str(index + 1) for index in range(len(value.perspectives))],
        )
    return _PerspectivesContainer(perspectives=[
        ScenePerspectiveOutput(
            **perspective.model_dump(mode="json"),
            scene_id=scene_id,
            evidence_ids=[f"scene:{scene_id}"],
        )
        for perspective, scene_id in zip(value.perspectives, scene_ids, strict=True)
    ])


def _bind_llm_enrichments(
    value: BaseModel, scene_ids: list[str], profile_targets: dict[str, list[str]],
) -> SceneEnrichmentsOutput:
    """Attach canonical references after validating the compact model contract."""
    if not isinstance(value, _SceneEnrichmentsLLMOutput):
        raise EmbodimentGenerationError("invalid psychological-analysis result")
    if len(value.scene_enrichments) != len(scene_ids):
        raise EmbodimentGenerationError(
            "psychological analysis must return exactly one enrichment per perspective",
            category="schema", expected_sequence=scene_ids,
            actual_sequence=[str(index + 1) for index in range(len(value.scene_enrichments))],
        )
    if len(value.scene_enrichments) != len(scene_ids):
        raise EmbodimentGenerationError("psychological analysis scene count mismatch", category="schema")
    records = []
    for scene_id, enrichment in zip(scene_ids, value.scene_enrichments, strict=True):
        evidence_id = f"scene:{scene_id}"
        records.append({
            "scene_id": scene_id,
            "evidence_ids": [evidence_id],
            "emotions": [item.model_dump(mode="json") for item in enrichment.emotions],
            "beliefs": [item.model_dump(mode="json") for item in enrichment.beliefs],
            "profile_events": [
                {**event.model_dump(mode="json"), "scene_id": scene_id,
                 "evidence_ids": [evidence_id]}
                for event in enrichment.profile_events
            ],
            "trait_candidates": [],
        })
    return SceneEnrichmentsOutput.model_validate({"scene_enrichments": records})


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
