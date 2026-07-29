"""Split-phase per-source CharacterAgent embodiment generation.

Four-call pipeline per source group:
  1. Character incorporation
  2. Scene psychological enrichment
  3. Cross-scene observations
  4. Atomic axis, aspect, and goal update

The first three calls analyze a fixed profile snapshot. Orchestration may run
that phase concurrently across sources. The fourth call is applied in source
order against the cumulative profile.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.integrations.llm.json_repair import repair_json_text
from app.jobs.character_agent.embody_agent_prompts import (
    ENRICHMENT_PROMPT,
    OBSERVATIONS_PROMPT,
    PERSPECTIVE_PROMPT,
    PROFILE_UPDATE_PROMPT,
)
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    EmbodyAgentResult,
    AxisChangeData,
    LLMCallRecord,
    ProfileUpdateOutput,
    SceneInput,
    SceneEnrichmentsOutput,
    ScenePerspectiveBundleOutput,
    ScenePerspectiveOutput,
    SubtitleChangeProposal,
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
        attempt: int = 1,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.source_entity_id = source_entity_id
        self.source_entity_alias = source_entity_alias
        self.offending_ids = sorted(offending_ids or set())
        self.allowed_ids = sorted(allowed_ids or set())
        self.attempt = attempt
        self.retryable = retryable

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
    ):
        self._llm = UsageTracker(llm_client)
        self.character_incorporation_model = character_incorporation_model
        self.scene_interpretation_model = scene_interpretation_model
        self.character_update_model = character_update_model
        self.max_goals = max_goals
        self.max_aspects = max_aspects
        self.semantic_correction_attempts = semantic_correction_attempts
        self.semantic_correction_count = 0

    @property
    def llm_calls(self) -> list[LLMCallRecord]:
        return self._llm.calls

    @property
    def stage_elapsed_seconds(self) -> dict[str, float]:
        return dict(self._llm.stage_elapsed_seconds)

    @staticmethod
    def _parse(schema: type[BaseModel], raw: str, stage: str) -> BaseModel:
        try:
            parsed = parse_json_deterministically(raw)
            dropped = _drop_ungrounded_output_items(parsed, schema=schema)
            if dropped:
                logger.warning(
                    "embodiment_ungrounded_items_dropped stage=%s count=%d",
                    stage, dropped,
                )
            return schema.model_validate(parsed)
        except (TypeError, ValueError, ValidationError) as exc:
            raise EmbodimentGenerationError(f"invalid {stage} output") from exc

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    async def _call(
        self, *, prompt: str, payload: dict[str, Any], schema: type[BaseModel],
        stage: str, usage_tag: str, max_tokens: int, model: Any,
        semantic_validator: Callable[[BaseModel], None] | None = None,
        source_entity_id: str | None = None,
        source_entity_alias: str | None = None,
    ) -> BaseModel:
        try:
            raw = await self._llm.chat(
                stage=stage,
                usage_tag=usage_tag,
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": self._json(payload)},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise EmbodimentGenerationError(
                f"{stage} transport failed",
                category="transport",
                stage=stage,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
                retryable=True,
            ) from exc
        try:
            result = self._parse(schema, str(raw), stage)
        except EmbodimentGenerationError as exc:
            repaired = await repair_json_text(
                llm_client=self._llm.llm, model=model,
                malformed_text=str(raw),
                schema_hint=json.dumps(schema.model_json_schema()),
                usage_tag=f"{usage_tag}.repair",
            )
            try:
                result = self._parse(schema, repaired, f"repaired {stage}")
            except EmbodimentGenerationError as repaired_exc:
                raise EmbodimentGenerationError(
                    f"{stage} schema validation failed",
                    category="schema",
                    stage=stage,
                    source_entity_id=source_entity_id,
                    source_entity_alias=source_entity_alias,
                    retryable=True,
                ) from repaired_exc

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
                    corrected_raw = await self._llm.chat(
                        stage=stage,
                        usage_tag=f"{usage_tag}.semantic_correction",
                        model=model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": self._json(correction_payload)},
                        ],
                        temperature=0.0,
                        max_tokens=max_tokens,
                    )
                except Exception as correction_exc:
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
                        schema, str(corrected_raw), f"corrected {stage}"
                    )
                except EmbodimentGenerationError as corrected_exc:
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

    async def analyze(
        self,
        *,
        source_entity_id: str,
        source_entity_alias: str,
        canonical_identity: dict[str, Any],
        current_behavioural_axes: dict[str, int],
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        scenes: list[SceneInput],
        on_stage: Any = None,
        stage_checkpoints: dict[str, dict[str, Any]] | None = None,
        on_checkpoint: Any = None,
    ) -> EmbodyAgentAnalysis:
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")

        scene_list = [s.model_dump(mode="json") for s in scenes]
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
        if "character_incorporation" in stage_checkpoints:
            perspectives_result = _PerspectivesContainer.model_validate(
                stage_checkpoints["character_incorporation"]
            )
        else:
            perspectives_result = await self._call(
                prompt=PERSPECTIVE_PROMPT,
                payload={
                    "identity": identity,
                    "current_profile": {
                        "behavioural_axes": current_behavioural_axes,
                        "aspects": aspects,
                        "goals": goals,
                    },
                    "scenes": scene_list,
                },
                schema=_PerspectivesContainer,
                stage="character incorporation",
                usage_tag="character_agent.embodiment.character_incorporation",
                max_tokens=max(3_000, 800 * len(scenes)),
                model=self.character_incorporation_model,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
            )
        perspectives = perspectives_result.perspectives
        expected_ids = [s.scene_id for s in scenes]
        actual_ids = [p.scene_id for p in perspectives]
        if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
            raise EmbodimentGenerationError(
                "perspective output scene_ids must match input scene order and be unique"
            )
        if "character_incorporation" not in stage_checkpoints and on_checkpoint:
            await on_checkpoint("character_incorporation", perspectives_result)

        # Step 2 — Per-scene psychological enrichment. Reflection is excluded.
        if on_stage:
            await on_stage(
                "source:{0} - Step 2: Psychological enrichment".format(source_entity_alias), [2]
            )
        if "scene_interpretation" in stage_checkpoints:
            enrichment_result = SceneEnrichmentsOutput.model_validate(
                stage_checkpoints["scene_interpretation"]
            )
        else:
            enrichment_result = await self._call(
                prompt=ENRICHMENT_PROMPT,
                payload={
                    "scenes": scene_list,
                    "perspectives": [
                        p.model_dump(
                            mode="json",
                            exclude={"character_reflection", "status"},
                        )
                        for p in perspectives
                    ],
                    "current_profile": {
                        "aspects": [{"id": a["id"], "name": a["name"]} for a in aspects],
                        "goals": [{"id": g["id"], "title": g["title"]} for g in goals],
                    },
                },
                schema=SceneEnrichmentsOutput,
                stage="scene psychological enrichment",
                usage_tag="character_agent.embodiment.scene_interpretation",
                max_tokens=max(3_000, 900 * len(scenes)),
                model=self.scene_interpretation_model,
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
            )
        enrichments = enrichment_result.scene_enrichments
        enrichment_ids = [item.scene_id for item in enrichments]
        if enrichment_ids != expected_ids or len(enrichment_ids) != len(set(enrichment_ids)):
            raise EmbodimentGenerationError(
                "enrichment output scene_ids must match input scene order and be unique"
            )
        aspect_ids = {item["id"] for item in aspects}
        goal_ids = {item["id"] for item in goals}
        for enrichment in enrichments:
            for impact in enrichment.impacts:
                permitted_ids = (
                    goal_ids if impact.impact_type.value == "goal_change" else aspect_ids
                )
                if impact.target_id not in permitted_ids:
                    raise EmbodimentGenerationError(
                        "scene enrichment referenced an unknown profile target"
                    )
        if "scene_interpretation" not in stage_checkpoints and on_checkpoint:
            await on_checkpoint("scene_interpretation", enrichment_result)

        bundles = [
            ScenePerspectiveBundleOutput(
                **perspective.model_dump(mode="json"),
                emotions=enrichment.emotions,
                beliefs=enrichment.beliefs,
                impacts=enrichment.impacts,
            )
            for perspective, enrichment in zip(perspectives, enrichments, strict=True)
        ]

        # Step 3 — Cross-scene observations. Reflection remains excluded.
        if on_stage:
            await on_stage(
                "source:{0} - Step 3: Cross-scene observations".format(source_entity_alias), [3]
            )
        observations_payload = {
            "identity": identity,
            "allowed_evidence_ids": sorted(known),
            "scene_bundles": [
                    {
                        "scene": scene,
                        "perspective": bundle.model_dump(
                            mode="json",
                            exclude={
                                "character_reflection", "status",
                                "emotions", "beliefs", "impacts",
                            },
                        ),
                        "emotions": [
                            item.model_dump(mode="json") for item in bundle.emotions
                        ],
                        "beliefs": [
                            item.model_dump(mode="json") for item in bundle.beliefs
                        ],
                        "impacts": [
                            item.model_dump(mode="json") for item in bundle.impacts
                        ],
                    }
                    for scene, bundle in zip(scene_list, bundles, strict=True)
            ],
        }
        if "observations" in stage_checkpoints:
            observations = EmbodimentObservationsOutput.model_validate(
                stage_checkpoints["observations"]
            )
            _validate_and_normalize_evidence(
                observations, allowed_ids=known, stage="cross-scene observations",
            )
        else:
            observations = await self._call(
                prompt=OBSERVATIONS_PROMPT,
                payload=observations_payload,
                schema=EmbodimentObservationsOutput,
                stage="cross-scene observations",
                usage_tag="character_agent.embodiment.observations",
                max_tokens=4_000,
                model=self.scene_interpretation_model,
                semantic_validator=lambda value: _validate_and_normalize_evidence(
                    value, allowed_ids=known, stage="cross-scene observations",
                ),
                source_entity_id=source_entity_id,
                source_entity_alias=source_entity_alias,
            )
        if "observations" not in stage_checkpoints and on_checkpoint:
            await on_checkpoint("observations", observations)

        return EmbodyAgentAnalysis(
            source_entity_id=source_entity_id,
            source_entity_alias=str(source_entity_alias),
            perspectives=bundles,
            observations=observations,
            subtitle_change=observations.subtitle_change or SubtitleChangeProposal(),
            evidence_ids=known,
            llm_calls=list(self.llm_calls),
        )

    async def apply_profile_update(
        self,
        *,
        analysis: EmbodyAgentAnalysis,
        current_behavioural_axes: dict[str, int],
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        on_stage: Any = None,
    ) -> EmbodyAgentResult:
        """Apply one analyzed source to the latest chronological profile."""

        obs_data = analysis.observations.model_dump(mode="json")

        # Step 4 — Atomic axis, aspect, and goal update
        if on_stage:
            await on_stage(
                "source:{0} - Step 4: Profile updates".format(
                    analysis.source_entity_alias
                ),
                [4],
            )

        profile_result = await self._call(
            prompt=PROFILE_UPDATE_PROMPT,
            payload={
                "current_profile": {
                    "behavioural_axes": current_behavioural_axes,
                    "aspects": [
                        {"name": a.get("name", ""), "category": a.get("category", ""),
                         "description": a.get("description"),
                         "importance": a.get("importance"), "intensity": a.get("intensity"),
                         "created_at": a.get("created_at")}
                        for a in current_aspects
                    ],
                    "goals": [
                        {"title": g.get("title", ""), "description": g.get("description", ""),
                         "goal_type": g.get("goal_type", ""),
                         "priority": g.get("priority"), "commitment": g.get("commitment"),
                         "created_at": g.get("created_at")}
                        for g in current_goals
                    ],
                },
                "observations": obs_data,
                "allowed_evidence_ids": sorted(analysis.evidence_ids),
                "limits": {
                    "max_aspects": self.max_aspects,
                    "max_goals": self.max_goals,
                },
            },
            schema=ProfileUpdateOutput,
            stage="profile updates",
            usage_tag="character_agent.embodiment.profile_update",
            max_tokens=6_000,
            model=self.character_update_model,
            semantic_validator=lambda value: _validate_profile_update(
                value,
                allowed_ids=analysis.evidence_ids,
                current_axes=current_behavioural_axes,
            ),
            source_entity_id=analysis.source_entity_id,
            source_entity_alias=analysis.source_entity_alias,
        )
        axis_updates = [
            AxisChangeData(
                axis=update.axis,
                new_value=max(
                    0, min(100, current_behavioural_axes[update.axis] + update.delta)
                ),
                justification=update.justification,
                confidence=update.confidence,
                evidence_ids=update.evidence_ids,
            )
            for update in profile_result.behavioural_axis_updates
        ]

        return EmbodyAgentResult(
            source_entity_id=analysis.source_entity_id,
            source_entity_alias=analysis.source_entity_alias,
            perspectives=analysis.perspectives,
            observations=analysis.observations,
            axis_updates=axis_updates,
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
        current_behavioural_axes: dict[str, int],
        current_aspects: list[dict[str, Any]],
        current_goals: list[dict[str, Any]],
        scenes: list[SceneInput],
        on_stage: Any = None,
    ) -> EmbodyAgentResult:
        """Compatibility entry point for one source executed end to end."""

        analysis = await self.analyze(
            source_entity_id=source_entity_id,
            source_entity_alias=source_entity_alias,
            canonical_identity=canonical_identity,
            current_behavioural_axes=current_behavioural_axes,
            current_aspects=current_aspects,
            current_goals=current_goals,
            scenes=scenes,
            on_stage=on_stage,
        )
        return await self.apply_profile_update(
            analysis=analysis,
            current_behavioural_axes=current_behavioural_axes,
            current_aspects=current_aspects,
            current_goals=current_goals,
            on_stage=on_stage,
        )


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
    "behavioural_axis_updates", "aspect_updates", "goal_updates",
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
    return normalized if normalized.startswith("scene:") else f"scene:{normalized}"


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
            else:
                _normalize_evidence_ids(value)


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


def _validate_profile_update(
    value: BaseModel,
    *,
    allowed_ids: set[str],
    current_axes: dict[str, int],
) -> None:
    _validate_and_normalize_evidence(
        value, allowed_ids=allowed_ids, stage="profile updates",
    )
    profile = value
    ineffective = {
        update.axis
        for update in profile.behavioural_axis_updates
        if max(0, min(100, current_axes[update.axis] + update.delta))
        == current_axes[update.axis]
    }
    if ineffective:
        raise EmbodimentGenerationError(
            "profile updates contained ineffective boundary deltas",
            category="semantic_update",
            stage="profile updates",
            offending_ids=ineffective,
            allowed_ids=set(current_axes),
        )
    ProfileUpdateOutput,
