"""Two-stage, graph-grounded CharacterAgent query orchestration."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from pydantic import ValidationError as PydanticValidationError

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.prompts import (
    DELIBERATION_PROMPT,
    FRAME_PROMPT,
    GENERIC_QUERY_PROMPT,
)
from app.jobs.character_agent.schemas import CharacterDeliberation, CharacterQueryFrame
from app.jobs.shrecknet.agent import parse_json_deterministically, repair_invalid_json
from app.schemas.character_agent import CharacterAgentQueryRequest, CharacterAgentQueryResult


StageReporter = Callable[[str, float], Awaitable[None]]

TRAIT_EXPLANATIONS = {
    "calm_aggressive": "0 means calm; 100 means aggressive.",
    "cautious_reckless": "0 means cautious; 100 means reckless.",
    "compassionate_ruthless": "0 means compassionate; 100 means ruthless.",
    "trusting_suspicious": "0 means trusting; 100 means suspicious.",
    "honest_deceptive": "0 means honest; 100 means deceptive.",
    "patient_impulsive": "0 means patient; 100 means impulsive.",
    "humble_proud": "0 means humble; 100 means proud.",
    "cooperative_dominating": "0 means cooperative; 100 means dominating.",
}


class CharacterGenerationError(RuntimeError):
    """A generation stage could not satisfy its deterministic contract."""


class CharacterAgentQueryJob:
    def __init__(
        self,
        *,
        llm_client: ShreckLLMClient,
        framing_model: LLMModelTarget,
        deliberation_model: LLMModelTarget,
        verification_model: LLMModelTarget,
        report_stage: StageReporter | None = None,
    ) -> None:
        self.llm = llm_client
        self.framing_model = framing_model
        self.deliberation_model = deliberation_model
        # Preserve the configured verification target as the JSON repair target.
        self.repair_model = verification_model
        self.report_stage = report_stage

    async def _report(self, stage: str, progress: float) -> None:
        if self.report_stage is not None:
            await self.report_stage(stage, progress)

    @staticmethod
    def _json(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _parse_frame(raw: str) -> CharacterQueryFrame:
        try:
            return CharacterQueryFrame.model_validate(parse_json_deterministically(raw))
        except (ValueError, PydanticValidationError) as exc:
            raise CharacterGenerationError(
                "task framing returned invalid structured output"
            ) from exc

    @staticmethod
    def _validate_content(request: CharacterAgentQueryRequest, content: Any) -> None:
        if request.response_format.type == "text":
            if not isinstance(content, str):
                raise CharacterGenerationError("text response did not render as text")
            return
        schema = request.response_format.schema_
        if schema is not None:
            try:
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(content)
            except (SchemaError, ValidationError) as exc:
                raise CharacterGenerationError(
                    "final response does not satisfy the requested schema"
                ) from exc

    @classmethod
    def _parse_final(
        cls, request: CharacterAgentQueryRequest, raw: str
    ) -> CharacterDeliberation:
        try:
            value = CharacterDeliberation.model_validate(
                parse_json_deterministically(raw)
            )
            cls._validate_content(request, value.content)
            return value
        except (ValueError, PydanticValidationError, CharacterGenerationError) as exc:
            raise CharacterGenerationError(
                "final response returned invalid structured output"
            ) from exc

    @staticmethod
    def _frame_profile(snapshot: dict[str, Any]) -> dict[str, Any]:
        character = snapshot["character_agent"]
        return {
            "name": character["name"],
            "behavioural_traits": character["behavioural_traits"],
            "trait_adherence": character["trait_adherence"],
            "active_aspects": [
                {"id": item["id"], "name": item["name"]}
                for item in snapshot["aspects"]
            ],
            "active_goals": [
                {
                    "id": item["id"],
                    "name": item.get("title") or item.get("name") or "",
                    "description": item.get("description") or "",
                }
                for item in snapshot["goals"]
            ],
        }

    @staticmethod
    def _validate_selectors(
        frame: CharacterQueryFrame, snapshot: dict[str, Any]
    ) -> None:
        aspect_ids = {item["id"] for item in snapshot["aspects"]}
        goal_ids = {item["id"] for item in snapshot["goals"]}
        if not set(frame.relevant_aspect_ids) <= aspect_ids:
            raise CharacterGenerationError("task framing referenced unknown aspects")
        if not set(frame.relevant_goal_ids) <= goal_ids:
            raise CharacterGenerationError("task framing referenced unknown goals")

    @staticmethod
    def _deliberation_input(
        request: CharacterAgentQueryRequest,
        frame: CharacterQueryFrame,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        selected_aspects = set(frame.relevant_aspect_ids)
        selected_goals = set(frame.relevant_goal_ids)
        traits = snapshot["character_agent"]["behavioural_traits"]
        return {
            "query": request.query,
            "context_summary": frame.context_summary,
            "system_instruction": request.system_instruction,
            "relevant_trait_axes": [
                {
                    "name": name,
                    "value": traits[name],
                    "explanation": TRAIT_EXPLANATIONS[name],
                }
                for name in frame.relevant_trait_axes
            ],
            "relevant_aspect_names": [
                item["name"]
                for item in snapshot["aspects"]
                if item["id"] in selected_aspects
            ],
            "relevant_goal_names": [
                item.get("title") or item.get("name") or ""
                for item in snapshot["goals"]
                if item["id"] in selected_goals
            ],
            "conflicts": frame.conflicts,
            "unknowns": frame.unknowns,
            "response_format": request.response_format.model_dump(
                mode="json", by_alias=True
            ),
        }

    def _repair_schema_hint(self, request: CharacterAgentQueryRequest) -> str:
        return self._json({
            "envelope": CharacterDeliberation.model_json_schema(),
            "content_contract": request.response_format.model_dump(
                mode="json", by_alias=True
            ),
        })

    async def _parse_or_repair(
        self, request: CharacterAgentQueryRequest, raw: str
    ) -> CharacterDeliberation:
        await self._report("validating", 0.85)
        try:
            return self._parse_final(request, raw)
        except CharacterGenerationError:
            await self._report("repairing", 0.9)
            repaired = await repair_invalid_json(
                llm_client=self.llm,
                model=self.repair_model,
                malformed_text=raw,
                schema_hint=self._repair_schema_hint(request),
                usage_tag="character_agent.repair",
            )
            await self._report("validating", 0.95)
            try:
                return self._parse_final(request, repaired)
            except CharacterGenerationError as exc:
                raise CharacterGenerationError(
                    "the repaired response does not satisfy the requested schema"
                ) from exc

    async def _run_generic(
        self, request: CharacterAgentQueryRequest
    ) -> CharacterAgentQueryResult:
        await self._report("deliberating", 0.35)
        raw = await self.llm.chat(
            model=self.deliberation_model,
            messages=[
                {"role": "system", "content": GENERIC_QUERY_PROMPT},
                {"role": "user", "content": self._json({
                    "query": request.query,
                    "context": request.context,
                    "system_instruction": request.system_instruction,
                    "response_format": request.response_format.model_dump(
                        mode="json", by_alias=True
                    ),
                })},
            ],
            temperature=request.generation.temperature,
            usage_tag="character_agent.generic",
        )
        result = await self._parse_or_repair(request, str(raw))
        return CharacterAgentQueryResult(
            type=request.response_format.type,
            content=result.content,
            decision_basis=result.decision_basis,
        )

    async def run(
        self,
        request: CharacterAgentQueryRequest,
        snapshot: dict[str, Any] | None = None,
    ) -> CharacterAgentQueryResult:
        if not request.use_character_identity:
            return await self._run_generic(request)
        if snapshot is None:
            raise CharacterGenerationError(
                "character identity snapshot is required for identity-grounded queries"
            )

        await self._report("framing", 0.2)
        frame_raw = await self.llm.chat(
            model=self.framing_model,
            messages=[
                {"role": "system", "content": FRAME_PROMPT},
                {"role": "user", "content": self._json({
                    "query": request.query,
                    "context": request.context,
                    "agent_profile": self._frame_profile(snapshot),
                })},
            ],
            temperature=0.0,
            usage_tag="character_agent.frame",
        )
        frame = self._parse_frame(str(frame_raw))
        self._validate_selectors(frame, snapshot)

        await self._report("deliberating", 0.55)
        deliberation_raw = await self.llm.chat(
            model=self.deliberation_model,
            messages=[
                {"role": "system", "content": DELIBERATION_PROMPT},
                {"role": "user", "content": self._json(
                    self._deliberation_input(request, frame, snapshot)
                )},
            ],
            temperature=request.generation.temperature,
            usage_tag="character_agent.deliberate",
        )
        result = await self._parse_or_repair(request, str(deliberation_raw))
        return CharacterAgentQueryResult(
            type=request.response_format.type,
            content=result.content,
            decision_basis=result.decision_basis,
        )
