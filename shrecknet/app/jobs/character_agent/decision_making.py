"""Single-deliberation, owner-memory-grounded CharacterAgent decision making."""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from pydantic import ValidationError as PydanticValidationError

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.structured_output import strict_json_schema
from app.jobs.character_agent.memory import select_relevant_memories
from app.jobs.character_agent.prompts import DECISION_MAKING_PROMPT, GENERIC_DECISION_MAKING_PROMPT
from app.jobs.character_agent.schemas import CharacterDeliberation
from app.jobs.shrecknet.agent import parse_json_deterministically, repair_invalid_json
from app.schemas.character_agent import CharacterAgentQueryRequest, CharacterAgentQueryResult, IdentityDescription

StageReporter = Callable[[str, float], Awaitable[None]]
RATIONALE_MAX_CHARACTERS = 2_000


class CharacterGenerationError(RuntimeError):
    """A generation stage could not satisfy its deterministic contract."""


class CharacterIdentityUnavailableError(RuntimeError):
    """Identity-mode deliberation requires a persisted identity description."""


class CharacterAgentDecisionMakingJob:
    def __init__(
        self, *, llm_client: ShreckLLMClient, deliberation_model: LLMModelTarget,
        repair_model: LLMModelTarget, framing_model: LLMModelTarget | None = None,
        report_stage: StageReporter | None = None,
    ) -> None:
        self.llm = llm_client
        self.deliberation_model = deliberation_model
        self.repair_model = repair_model
        self.framing_model = framing_model  # Legacy constructor compatibility; never used.
        self.report_stage = report_stage

    async def _report(self, stage: str, progress: float) -> None:
        if self.report_stage:
            await self.report_stage(stage, progress)

    @staticmethod
    def _json(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _cap_rationale(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: child[:RATIONALE_MAX_CHARACTERS]
                if key == "rationale" and isinstance(child, str)
                else CharacterAgentDecisionMakingJob._cap_rationale(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [CharacterAgentDecisionMakingJob._cap_rationale(item) for item in value]
        return value

    @staticmethod
    def _response_schema(request: CharacterAgentQueryRequest) -> dict[str, Any] | None:
        if request.response_format.schema_ is None:
            return None
        normalized = copy.deepcopy(request.response_format.schema_)

        def apply(item: Any) -> None:
            if isinstance(item, dict):
                properties = item.get("properties")
                if isinstance(properties, dict) and isinstance(properties.get("rationale"), dict):
                    properties["rationale"]["maxLength"] = RATIONALE_MAX_CHARACTERS
                for child in item.values():
                    apply(child)
            elif isinstance(item, list):
                for child in item:
                    apply(child)

        apply(normalized)
        return normalized

    @classmethod
    def _response_format_payload(cls, request: CharacterAgentQueryRequest) -> dict[str, Any]:
        value = request.response_format.model_dump(mode="json", by_alias=True)
        if request.response_format.type == "json":
            value["schema"] = cls._response_schema(request)
        return value

    @classmethod
    def _validate_content(cls, request: CharacterAgentQueryRequest, content: Any) -> None:
        if request.response_format.type == "text":
            if not isinstance(content, str):
                raise CharacterGenerationError("text response did not render as text")
            return
        schema = cls._response_schema(request)
        if schema is not None:
            try:
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(content)
            except (SchemaError, ValidationError) as exc:
                raise CharacterGenerationError(
                    "final response does not satisfy the requested schema"
                ) from exc

    @classmethod
    def _parse_final(cls, request: CharacterAgentQueryRequest, raw: str) -> CharacterDeliberation:
        try:
            value = CharacterDeliberation.model_validate(parse_json_deterministically(raw))
            value.content = cls._cap_rationale(value.content)
            cls._validate_content(request, value.content)
            return value
        except (ValueError, PydanticValidationError, CharacterGenerationError) as exc:
            raise CharacterGenerationError("final response returned invalid structured output") from exc

    def _envelope_response_format(self, request: CharacterAgentQueryRequest) -> dict[str, Any]:
        content = {"type": "string"} if request.response_format.type == "text" else (
            self._response_schema(request) or {}
        )
        return strict_json_schema("character_agent_decision_making", {
            "type": "object",
            "additionalProperties": False,
            "required": ["content", "decision_basis"],
            "properties": {
                "content": content,
                "decision_basis": {"type": "string", "maxLength": RATIONALE_MAX_CHARACTERS},
            },
        })

    async def _parse_or_repair(self, request: CharacterAgentQueryRequest, raw: str) -> CharacterDeliberation:
        await self._report("validating", .85)
        try:
            return self._parse_final(request, raw)
        except CharacterGenerationError:
            await self._report("repairing", .9)
            repaired = await repair_invalid_json(
                llm_client=self.llm, model=self.repair_model, malformed_text=raw,
                schema_hint=self._json(self._envelope_response_format(request)["json_schema"]["schema"]),
                usage_tag="character_agent.repair",
            )
            await self._report("validating", .95)
            return self._parse_final(request, repaired)

    async def run(
        self, request: CharacterAgentQueryRequest, snapshot: dict[str, Any] | None = None,
    ) -> CharacterAgentQueryResult:
        if request.use_character_identity:
            if snapshot is None:
                raise CharacterGenerationError("character identity snapshot is required for identity-grounded queries")
            identity = snapshot["character_agent"].get("identity_description")
            if identity is None:
                raise CharacterIdentityUnavailableError("CharacterAgent identity_description is unavailable")
            identity = IdentityDescription.model_validate(identity).model_dump(mode="json")
            await self._report("retrieving_memories", .2)
            payload = {
                "identity_description": identity,
                "memories": await select_relevant_memories(
                    query=request.query,
                    context=request.context,
                    memories=snapshot.get("memories", []),
                ),
                "query": request.query,
                "context": request.context,
                "instruction": request.system_instruction,
                "response_format": self._response_format_payload(request),
            }
            prompt, usage_tag = DECISION_MAKING_PROMPT, "character_agent.decision_making"
        else:
            payload = {
                "query": request.query, "context": request.context,
                "instruction": request.system_instruction,
                "response_format": self._response_format_payload(request),
            }
            prompt, usage_tag = GENERIC_DECISION_MAKING_PROMPT, "character_agent.generic_decision_making"

        await self._report("deliberating", .55)
        # Do not fall back to an unstructured second deliberation call. A
        # malformed result gets the one bounded JSON-repair pass below.
        raw = await self.llm.chat(
            model=self.deliberation_model,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": self._json(payload)}],
            temperature=request.generation.temperature, usage_tag=usage_tag,
            response_format=self._envelope_response_format(request),
        )
        result = await self._parse_or_repair(request, str(raw))
        return CharacterAgentQueryResult(
            type=request.response_format.type, content=result.content,
            decision_basis=result.decision_basis,
        )
