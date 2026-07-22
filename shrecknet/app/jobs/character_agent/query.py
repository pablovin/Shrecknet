"""Three-call, graph-grounded CharacterAgent query orchestration."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from pydantic import ValidationError as PydanticValidationError

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.prompts import DELIBERATION_PROMPT, FRAME_PROMPT, VERIFY_PROMPT
from app.jobs.character_agent.schemas import CharacterDeliberation, CharacterQueryFrame, VerifiedRendering
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import CharacterAgentQueryRequest, CharacterAgentQueryResponse


class CharacterGenerationError(RuntimeError):
    """A generation stage could not satisfy its deterministic contract."""


class CharacterAgentQueryJob:
    def __init__(self, *, llm_client: ShreckLLMClient, framing_model: LLMModelTarget,
                 deliberation_model: LLMModelTarget, verification_model: LLMModelTarget) -> None:
        self.llm = llm_client
        self.framing_model = framing_model
        self.deliberation_model = deliberation_model
        self.verification_model = verification_model

    @staticmethod
    def _parse(model_type, raw: str, stage: str):
        try:
            return model_type.model_validate(parse_json_deterministically(raw))
        except (ValueError, PydanticValidationError) as exc:
            raise CharacterGenerationError(f"{stage} returned invalid structured output") from exc

    @staticmethod
    def _json(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _validate_refs(frame: CharacterQueryFrame, snapshot: dict[str, Any]) -> None:
        aspect_ids = {item["id"] for item in snapshot["aspects"]}
        goal_ids = {item["id"] for item in snapshot["goals"]}
        if not set(frame.relevant_aspect_ids) <= aspect_ids or not set(frame.relevant_goal_ids) <= goal_ids:
            raise CharacterGenerationError("task framing referenced unknown character evidence")

    @staticmethod
    def _validate_options(frame: CharacterQueryFrame, request: CharacterAgentQueryRequest) -> None:
        supplied = json.dumps(
            {"query": request.query, "context": request.context}, ensure_ascii=False
        ).casefold()
        if any(option.casefold() not in supplied for option in frame.explicit_options):
            raise CharacterGenerationError("task framing invented an option not supplied by the caller")

    @staticmethod
    def _evidence(frame: CharacterQueryFrame, snapshot: dict[str, Any]) -> dict[str, Any]:
        selected_aspects = set(frame.relevant_aspect_ids)
        selected_goals = set(frame.relevant_goal_ids)
        selected_traits = {item.trait for item in frame.relevant_trait_axes}
        character = snapshot["character_agent"]
        return {
            "character": {
                "name": character["name"],
                "background_story": character["background_story"],
                "trait_adherence": character["trait_adherence"],
                "behavioural_traits": {
                    key: value for key, value in character["behavioural_traits"].items()
                    if key in selected_traits
                },
            },
            "aspects": [item for item in snapshot["aspects"] if item["id"] in selected_aspects],
            "goals": [item for item in snapshot["goals"] if item["id"] in selected_goals],
        }

    async def run(self, request: CharacterAgentQueryRequest, snapshot: dict[str, Any]) -> CharacterAgentQueryResponse:
        public_request = request.model_dump(mode="json", by_alias=True)
        frame_raw = await self.llm.chat(
            model=self.framing_model,
            messages=[{"role": "system", "content": FRAME_PROMPT}, {"role": "user", "content": self._json({
                "request": public_request, "complete_character_profile": snapshot,
                "required_output": CharacterQueryFrame.model_json_schema(),
            })}], temperature=0.0, max_tokens=min(request.generation.max_tokens, 1500),
            usage_tag="character_agent.frame",
        )
        frame = self._parse(CharacterQueryFrame, str(frame_raw), "task framing")
        self._validate_refs(frame, snapshot)
        self._validate_options(frame, request)
        evidence = self._evidence(frame, snapshot)

        deliberation_raw = await self.llm.chat(
            model=self.deliberation_model,
            messages=[{"role": "system", "content": DELIBERATION_PROMPT}, {"role": "user", "content": self._json({
                "query": request.query, "context": request.context, "frame": frame.model_dump(),
                "relevant_character_evidence": evidence,
                "required_output": CharacterDeliberation.model_json_schema(),
            })}], temperature=request.generation.temperature, max_tokens=request.generation.max_tokens,
            usage_tag="character_agent.deliberate",
        )
        deliberation = self._parse(CharacterDeliberation, str(deliberation_raw), "character deliberation")
        permitted_ids = set(frame.relevant_aspect_ids) | set(frame.relevant_goal_ids)
        if any(not set(candidate.supporting_ids) <= permitted_ids for candidate in deliberation.candidate_responses):
            raise CharacterGenerationError("character deliberation referenced unknown evidence")

        verification_raw = await self.llm.chat(
            model=self.verification_model,
            messages=[{"role": "system", "content": VERIFY_PROMPT}, {"role": "user", "content": self._json({
                "query": request.query, "context": request.context,
                "task_instruction": request.system_instruction,
                "response_format": request.response_format.model_dump(mode="json", by_alias=True),
                "deliberation": deliberation.model_dump(), "supporting_evidence": evidence,
                "required_output": VerifiedRendering.model_json_schema(),
            })}], temperature=0.0, max_tokens=request.generation.max_tokens,
            usage_tag="character_agent.verify",
        )
        verified = self._parse(VerifiedRendering, str(verification_raw), "verification")
        if any(not set(item.supporting_ids) <= permitted_ids for item in verified.claim_assessments):
            raise CharacterGenerationError("verification referenced unknown evidence")
        unsupported = {
            item.claim for item in verified.claim_assessments
            if item.classification == "unsupported_claim"
        }
        if not unsupported <= set(verified.unsupported_claims_removed):
            raise CharacterGenerationError("verification did not mark every unsupported claim as removed")

        content = verified.rendered_response
        if request.response_format.type == "text":
            if not isinstance(content, str):
                raise CharacterGenerationError("text response did not render as text")
        else:
            schema = request.response_format.schema_
            if schema is not None:
                try:
                    Draft202012Validator.check_schema(schema)
                    Draft202012Validator(schema).validate(content)
                except (SchemaError, ValidationError) as exc:
                    raise CharacterGenerationError("final response does not satisfy the requested schema") from exc
        return CharacterAgentQueryResponse(type=request.response_format.type, content=content)
