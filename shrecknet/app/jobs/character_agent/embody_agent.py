"""Scene-centric source-boundary CharacterAgent embodiment generation.

Two LLM calls run for each source chunk:
  1. Character incorporation
  2. Scene psychological analysis and scene-local candidate extraction

The backend then validates, grounds, and reduces extracted candidates
deterministically. Source bundles run sequentially and accumulate grounded
evidence; each produces one scene-associated revision.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.integrations.llm.json_repair import repair_json_text
from app.integrations.llm.structured_output import (
    strict_json_schema,
    structured_output_is_unsupported,
)
from app.integrations.llm.shreckllm_client import LLMProviderUnavailableError
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.embody_agent_prompts import (
    BASELINE_PROMPT,
    PSYCHOLOGICAL_ANALYSIS_PROMPT,
    PERSPECTIVE_PROMPT,
    PERSPECTIVE_TRUNCATION_RECOVERY_PROMPT,
)
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    CharacterAspectCategory,
    CharacterBeliefStatus,
    CharacterGoalType,
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    EmbodyAgentResult,
    LLMCallRecord,
    ProfileUpdateOutput,
    SceneInput,
    SceneEnrichmentsOutput,
    ScenePerspectiveBundleOutput,
    ScenePerspectiveOutput,
    ScenePerspectiveSourceType,
    SubtitleChangeProposal,
)


from app.schemas.character_traits import TraitProfile, TraitEvidence, TraitProposal
from app.schemas.character_traits import TraitKey, ZValue
from app.services.character_trait_service import (
    ground_observations, merge_evidence, update_profile, validate_proposals, validate_scene_grounding, scene_digest,
)

logger = logging.getLogger(__name__)

# Embodiment works on chunks of at most five scenes. This ceiling is not an
# expected response size; it prevents a malformed response from consuming an
# unbounded provider-default completion budget.
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


class ScenePerspectiveLLMOutput(BaseModel):
    """Compact model-owned fields for one position-bound scene perspective."""

    model_config = ConfigDict(extra="forbid")

    source_type: ScenePerspectiveSourceType
    awareness_level: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=300)
    interpretation: str = Field(min_length=1, max_length=700)
    character_reflection: str = Field(min_length=1, max_length=500)
    memory_strength: int = Field(ge=0, le=100)
    importance: int = Field(ge=1, le=5)


class _PerspectivesLLMContainer(BaseModel):
    """LLM-facing incorporation contract; canonical references are backend-owned."""

    model_config = ConfigDict(extra="forbid")
    perspectives: list[ScenePerspectiveLLMOutput]


class _EmotionLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arousal: int = Field(ge=0, le=100)
    valence: int = Field(ge=-100, le=100)
    description: str = Field(min_length=1, max_length=240)


class _BeliefLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=300)
    confidence: int = Field(ge=0, le=100)
    status: CharacterBeliefStatus


class _ChoiceConditionLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["supported", "contradicted", "unknown"]
    justification: str = Field(min_length=1, max_length=240)


class _ChoiceConditionsLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge: _ChoiceConditionLLMOutput
    capability: _ChoiceConditionLLMOutput
    options: _ChoiceConditionLLMOutput
    freedom: _ChoiceConditionLLMOutput


class _TraitCandidateLLMOutput(BaseModel):
    """Model-owned trait observation fields; provenance is attached by the backend."""

    model_config = ConfigDict(extra="forbid")
    trait: TraitKey
    evidence_kind: Literal["behavior"] = "behavior"
    situation_type: str = Field(min_length=1, max_length=80)
    pole: Literal["left", "right"]
    update_intensity: Literal["small", "medium", "large"]
    expression_z: ZValue
    diagnosticity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    behavior: str = Field(min_length=1, max_length=300)
    justification: str = Field(min_length=1, max_length=360)
    conditions: _ChoiceConditionsLLMOutput
    comparison_context: str | None = Field(None, max_length=300)


class _AspectSignalLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str = Field(min_length=1, max_length=360)
    importance: int = Field(ge=1, le=5)
    justification: str = Field(min_length=1, max_length=360)
    confidence: float = Field(ge=0, le=1)


class _GoalSignalLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=360)
    goal_type: CharacterGoalType
    priority: int = Field(ge=0, le=100)
    commitment: int = Field(ge=0, le=100)
    basis: Literal["explicit", "inferred"]
    justification: str = Field(min_length=1, max_length=360)
    confidence: float = Field(ge=0, le=1)


class _GoalImpactLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impact_type: Literal["goal_change"]
    target_index: int = Field(ge=1)
    direction: Literal["advanced", "threatened"]
    magnitude: int = Field(ge=0, le=100)
    description: str = Field(min_length=1, max_length=300)


class _AspectImpactLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impact_type: Literal["aspect_change"]
    target_index: int = Field(ge=1)
    direction: Literal["created", "reinforced", "invalidated"]
    magnitude: int = Field(ge=0, le=100)
    description: str = Field(min_length=1, max_length=300)


class _SceneEnrichmentLLMOutput(BaseModel):
    """Bounded psychological-analysis content without persistence references."""

    model_config = ConfigDict(extra="forbid")
    emotions: list[_EmotionLLMOutput] = Field(max_length=2)
    beliefs: list[_BeliefLLMOutput] = Field(max_length=2)
    impacts: list[_GoalImpactLLMOutput | _AspectImpactLLMOutput] = Field(max_length=2)
    trait_candidates: list[_TraitCandidateLLMOutput] = Field(max_length=3)
    aspect_signals: list[_AspectSignalLLMOutput] = Field(max_length=1)
    goal_signals: list[_GoalSignalLLMOutput] = Field(max_length=1)


class _SceneEnrichmentsLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_enrichments: list[_SceneEnrichmentLLMOutput]


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
        max_goals: int = 10, max_aspects: int = 20,
        semantic_correction_attempts: int = 1,
        debug_artifacts: EmbodimentDebugArtifacts | None = None,
        debug_source_index: int | None = None,
        debug_source_alias: str | None = None,
    ):
        self._llm = UsageTracker(llm_client)
        self.character_incorporation_model = character_incorporation_model
        self.scene_interpretation_model = scene_interpretation_model
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
            usage_tag="character_agent.embodiment.baseline", max_tokens=EMBODIMENT_LLM_MAX_TOKENS,
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
        return await self._incorporate_perspectives(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            identity={key: canonical_identity.get(key) for key in (
                "alias", "subtitle", "entity_type", "entity_type_description", "properties",
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
        def stats(field: str) -> tuple[int, int, float]:
            lengths = [len(getattr(item, field)) for item in perspectives]
            return min(lengths), max(lengths), round(sum(lengths) / len(lengths), 1)

        metadata = self._llm.last_response_metadata
        summary = stats("summary")
        interpretation = stats("interpretation")
        reflection = stats("character_reflection")
        logger.info(
            "character_incorporation_completed source_id=%s source_alias=%s "
            "scene_count=%d perspective_count=%d provider=%s requested_model=%s "
            "resolved_model=%s reasoning_enabled=%s requested_max_tokens=%d "
            "prompt_tokens=%s completion_tokens=%s finish_reason=%s response_chars=%s "
            "duration_ms=%s summary_chars=%s interpretation_chars=%s reflection_chars=%s",
            source_entity_id, source_entity_alias, len(perspectives), len(perspectives),
            getattr(self.character_incorporation_model, "provider", None),
            getattr(self.character_incorporation_model, "name", self.character_incorporation_model),
            metadata.get("resolved_model"), metadata.get("reasoning_enabled"),
            EMBODIMENT_LLM_MAX_TOKENS, metadata.get("prompt_tokens"),
            metadata.get("completion_tokens"), metadata.get("finish_reason"),
            self._llm.calls[-1].output_chars if self._llm.calls else None,
            round((self._llm.last_elapsed_seconds or 0) * 1000, 2),
            summary, interpretation, reflection,
        )

    async def _analyze_psychological_batch(
        self, *, source_entity_id: str, source_entity_alias: str,
        perspectives: list[ScenePerspectiveOutput], aspects: list[dict[str, Any]],
        goals: list[dict[str, Any]], recovery_depth: int = 0,
    ) -> list[Any]:
        """Analyze one perspective batch, splitting only a truncated retry batch."""
        expected_ids = [perspective.scene_id for perspective in perspectives]
        profile_targets = {
            "aspects": [aspect["id"] for aspect in aspects],
            "goals": [goal["id"] for goal in goals],
        }
        payload = {
            "perspectives": [
                perspective.model_dump(
                    mode="json",
                    exclude={"scene_id", "evidence_ids", "character_reflection", "status"},
                ) | {"position": index + 1}
                for index, perspective in enumerate(perspectives)
            ],
            "current_profile": {
                "aspects": [{"position": index + 1, "name": aspect["name"]} for index, aspect in enumerate(aspects)],
                "goals": [{"position": index + 1, "title": goal["title"]} for index, goal in enumerate(goals)],
            },
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
                    "bind_references": False, "profile_targets": profile_targets,
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
                recovery_depth=recovery_depth + 1,
            )
            second = await self._analyze_psychological_batch(
                source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
                perspectives=perspectives[midpoint:], aspects=aspects, goals=goals,
                recovery_depth=recovery_depth + 1,
            )
            return [*first, *second]
        return _bind_llm_enrichments(llm_result, expected_ids, profile_targets).scene_enrichments

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
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")

        scene_list = [
            {"position": index + 1, "name": scene.name, "description": scene.description,
             "created_at": scene.created_at}
            for index, scene in enumerate(scenes)
        ]
        known = {f"scene:{s.scene_id}" for s in scenes}
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
        # Step 2 — Per-scene psychological enrichment. It consumes only the
        # already-grounded character perspective, never the raw objective scene.
        # Reflection remains presentation-only and cannot manufacture evidence.
        if on_stage:
            await on_stage(
                "source:{0} - Step 2: Psychological analysis".format(source_entity_alias), [2]
            )
        enrichments = await self._analyze_psychological_batch(
            source_entity_id=source_entity_id, source_entity_alias=source_entity_alias,
            perspectives=perspectives, aspects=aspects, goals=goals,
        )
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
    """Return the contract the model actually writes, not the persistence shape.

    Incorporation emits only model-owned perspective fields and has an exact
    position-bound array length. Impacts select a supplied profile target by
    one-based ``target_index``. The backend resolves that transient index to the
    persisted ``target_id`` before Pydantic validates the persistence schema.
    """
    result = copy.deepcopy(schema.model_json_schema())
    if schema is _PerspectivesLLMContainer:
        scene_ids = (output_binding or {}).get("scene_ids")
        perspectives = result.get("properties", {}).get("perspectives")
        if isinstance(scene_ids, list) and isinstance(perspectives, dict):
            perspectives["minItems"] = len(scene_ids)
            perspectives["maxItems"] = len(scene_ids)
        return result

    if schema is not _SceneEnrichmentsLLMOutput:
        return result
    definitions = result.get("$defs", {})
    enrichments = result.get("properties", {}).get("scene_enrichments")
    scene_ids = (output_binding or {}).get("scene_ids")
    if isinstance(scene_ids, list) and isinstance(enrichments, dict):
        enrichments["minItems"] = len(scene_ids)
        enrichments["maxItems"] = len(scene_ids)
    goal_impact = definitions.get("_GoalImpactLLMOutput")
    aspect_impact = definitions.get("_AspectImpactLLMOutput")
    enrichment = definitions.get("_SceneEnrichmentLLMOutput")
    if not all(isinstance(item, dict) for item in (goal_impact, aspect_impact, enrichment)):
        return result
    targets = (output_binding or {}).get("profile_targets") or {}
    aspect_count = len(targets.get("aspects", []))
    goal_count = len(targets.get("goals", []))
    impacts = enrichment.get("properties", {}).get("impacts")
    if isinstance(impacts, dict):
        if aspect_count == 0 and goal_count == 0:
            impacts["maxItems"] = 0
        else:
            for definition, count in ((goal_impact, goal_count), (aspect_impact, aspect_count)):
                properties = definition.get("properties", {})
                index = properties.get("target_index") if isinstance(properties, dict) else None
                if isinstance(index, dict) and count:
                    index["maximum"] = count
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
    parsed = value.model_dump(mode="json")
    _bind_model_output_references(
        parsed, collection="scene_enrichments", scene_ids=scene_ids,
        profile_targets=profile_targets,
    )
    try:
        return SceneEnrichmentsOutput.model_validate(parsed)
    except ValidationError as exc:
        raise EmbodimentGenerationError(
            "invalid bound psychological-analysis output", category="schema",
        ) from exc


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
            logger.info(
                "embodiment_duplicate_aspect_signal_ignored source_id=%s name=%s",
                source_entity_id, signal.name,
            )
            continue
        known_aspects.add(key)
        aspect_updates.append({"operation": "add", **signal.model_dump(mode="json")})
    goal_updates = []
    for signal in sorted(goal_signals, key=lambda item: (-item.confidence, -item.priority, item.title.casefold())):
        key = _normalized_identity_label(signal.title)
        if key in known_goals:
            logger.info(
                "embodiment_duplicate_goal_signal_ignored source_id=%s title=%s",
                source_entity_id, signal.title,
            )
            continue
        known_goals.add(key)
        goal_updates.append({"operation": "add", **signal.model_dump(mode="json")})
    return ProfileUpdateOutput.model_validate({
        "trait_proposals": proposals,
        "aspect_updates": aspect_updates[:2],
        "goal_updates": goal_updates[:1],
    })
