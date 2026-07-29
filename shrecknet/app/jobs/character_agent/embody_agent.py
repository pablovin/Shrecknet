"""Atomic per-source CharacterAgent embodiment generation.

Six-call pipeline per source group:
  1. Character incorporation
  2. Scene psychological enrichment
  3. Cross-scene observations
  4-6. Parallel axis, aspect, and goal updates
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.integrations.llm.json_repair import repair_json_text
from app.jobs.character_agent.embody_agent_prompts import (
    ASPECTS_UPDATE_PROMPT,
    AXES_UPDATE_PROMPT,
    GOALS_UPDATE_PROMPT,
    ENRICHMENT_PROMPT,
    OBSERVATIONS_PROMPT,
    PERSPECTIVE_PROMPT,
)
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    AxisChangeOutput,
    AspectUpdateOutput,
    GoalUpdateOutput,
    EmbodimentObservationsOutput,
    EmbodyAgentResult,
    LLMCallRecord,
    SceneInput,
    SceneEnrichmentsOutput,
    ScenePerspectiveBundleOutput,
    ScenePerspectiveOutput,
    SubtitleChangeProposal,
)

class EmbodimentGenerationError(RuntimeError):
    pass


class _PerspectivesContainer(BaseModel):
    perspectives: list[ScenePerspectiveOutput]


class UsageTracker:
    def __init__(self, llm_client):
        self.llm = llm_client
        self.calls: list[LLMCallRecord] = []

    async def chat(self, *, stage: str, usage_tag: str, **kwargs) -> str:
        input_text = json.dumps(kwargs.get("messages", []), ensure_ascii=False)
        input_chars = len(input_text)
        input_tokens_est = max(1, input_chars // 4)

        result = await self.llm.chat(usage_tag=usage_tag, **kwargs)

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
    ):
        self._llm = UsageTracker(llm_client)
        self.character_incorporation_model = character_incorporation_model
        self.scene_interpretation_model = scene_interpretation_model
        self.character_update_model = character_update_model
        self.max_goals = max_goals
        self.max_aspects = max_aspects

    @property
    def llm_calls(self) -> list[LLMCallRecord]:
        return self._llm.calls

    @staticmethod
    def _parse(schema: type[BaseModel], raw: str, stage: str) -> BaseModel:
        try:
            parsed = parse_json_deterministically(raw)
            return schema.model_validate(parsed)
        except (TypeError, ValueError, ValidationError) as exc:
            raise EmbodimentGenerationError(f"invalid {stage} output") from exc

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    async def _call(
        self, *, prompt: str, payload: dict[str, Any], schema: type[BaseModel],
        stage: str, usage_tag: str, max_tokens: int, model: Any,
    ) -> BaseModel:
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
        try:
            return self._parse(schema, str(raw), stage)
        except EmbodimentGenerationError:
            repaired = await repair_json_text(
                llm_client=self._llm.llm, model=model,
                malformed_text=str(raw),
                schema_hint=json.dumps(schema.model_json_schema()),
                usage_tag=f"{usage_tag}.repair",
            )
            return self._parse(schema, repaired, f"repaired {stage}")

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
        if not scenes:
            raise EmbodimentGenerationError("no scenes provided for embodiment")

        scene_list = [s.model_dump(mode="json") for s in scenes]
        known_raw = {s.scene_id for s in scenes}
        known = known_raw | {f"scene:{s.scene_id}" for s in scenes}

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
        )
        perspectives = perspectives_result.perspectives
        expected_ids = [s.scene_id for s in scenes]
        actual_ids = [p.scene_id for p in perspectives]
        if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
            raise EmbodimentGenerationError(
                "perspective output scene_ids must match input scene order and be unique"
            )

        # Step 2 — Per-scene psychological enrichment. Reflection is excluded.
        if on_stage:
            await on_stage(
                "source:{0} - Step 2: Psychological enrichment".format(source_entity_alias), [2]
            )
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
        observations = await self._call(
            prompt=OBSERVATIONS_PROMPT,
            payload={
                "identity": identity,
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
            },
            schema=EmbodimentObservationsOutput,
            stage="cross-scene observations",
            usage_tag="character_agent.embodiment.observations",
            max_tokens=4_000,
            model=self.scene_interpretation_model,
        )

        # Validate observation evidence IDs (accept both scene:id and bare id)
        obs_data = observations.model_dump(mode="json")
        observed_ids = _collect_evidence_ids(obs_data)
        if not observed_ids <= known:
            raise EmbodimentGenerationError(
                "observations referenced unknown evidence"
            )

        # Step 4 — Parallel axis, aspect, goal updates
        if on_stage:
            await on_stage(
                "source:{0} - Step 4: Axis & aspect & goal updates".format(source_entity_alias),
                [4],
            )

        axes_result, aspects_result, goals_result = await asyncio.gather(
            self._call(
                prompt=AXES_UPDATE_PROMPT,
                payload={
                    "current_axes": current_behavioural_axes,
                    "observations": obs_data,
                },
                schema=AxisChangeOutput,
                stage="axis updates",
                usage_tag="character_agent.embodiment.axes",
                max_tokens=2_500,
                model=self.character_update_model,
            ),
            self._call(
                prompt=ASPECTS_UPDATE_PROMPT,
                payload={
                    "current_aspects": [
                        {"name": a.get("name", ""), "category": a.get("category", ""),
                         "description": a.get("description"),
                         "importance": a.get("importance"), "intensity": a.get("intensity")}
                        for a in current_aspects
                    ],
                    "observations": obs_data,
                    "limits": {"max_aspects": self.max_aspects},
                },
                schema=AspectUpdateOutput,
                stage="aspect updates",
                usage_tag="character_agent.embodiment.aspects",
                max_tokens=3_500,
                model=self.character_update_model,
            ),
            self._call(
                prompt=GOALS_UPDATE_PROMPT,
                payload={
                    "current_goals": [
                        {"title": g.get("title", ""), "description": g.get("description", ""),
                         "goal_type": g.get("goal_type", ""),
                         "priority": g.get("priority"), "commitment": g.get("commitment")}
                        for g in current_goals
                    ],
                    "observations": obs_data,
                    "limits": {"max_goals": self.max_goals},
                },
                schema=GoalUpdateOutput,
                stage="goal updates",
                usage_tag="character_agent.embodiment.goals",
                max_tokens=3_500,
                model=self.character_update_model,
            ),
        )

        # Validate update evidence IDs (accept both scene:id and bare id)
        for result, name in (
            (axes_result, "axis"), (aspects_result, "aspect"), (goals_result, "goal"),
        ):
            referenced = _collect_evidence_ids(result.model_dump(mode="json"))
            if not referenced <= known:
                raise EmbodimentGenerationError(
                    f"{name} updates referenced unknown evidence"
                )

        return EmbodyAgentResult(
            source_entity_id=source_entity_id,
            source_entity_alias=str(source_entity_alias),
            perspectives=bundles,
            observations=observations,
            axis_updates=axes_result.behavioural_axes,
            aspect_updates=aspects_result.aspect_updates,
            goal_updates=goals_result.goal_updates,
            subtitle_change=observations.subtitle_change or SubtitleChangeProposal(),
            llm_calls=list(self.llm_calls),
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
