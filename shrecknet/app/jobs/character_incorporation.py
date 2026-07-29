"""Grounded character composition shared by Elder and Librarian.

Neutral synthesis owns atomic claims and citation attribution. Character
incorporation receives citation-free claims and may compose them into cohesive
passages. The backend validates complete claim coverage and restores trusted
source markers without exposing citation identifiers to the character model.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from app.core.config_store import LLMModelTarget


_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_CITATION_MARKUP = re.compile(r"\{cite\b|source-\d+|evidence-\d+", re.IGNORECASE)


def normalize_target_language(value: Any) -> str:
    """Return a conservative normalized BCP-47 tag or ``und``."""
    raw = str(value or "").strip()
    if raw.lower() == "und" or not _LANGUAGE_TAG.fullmatch(raw):
        return "und"
    parts = raw.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            normalized.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


class GroundedClaim(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_citations(self) -> "GroundedClaim":
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("claim text cannot be blank")
        if _CITATION_MARKUP.search(self.text):
            raise ValueError("claim text cannot contain citation identifiers or markup")
        self.citations = [citation.strip() for citation in self.citations]
        if any(not citation for citation in self.citations):
            raise ValueError("claim citation identifiers cannot be blank")
        if len(self.citations) != len(set(self.citations)):
            raise ValueError("claim citations must be unique")
        return self


class NeutralAnswer(BaseModel):
    claims: list[GroundedClaim] = Field(min_length=1)
    uncertainty: str | None = None

    @model_validator(mode="after")
    def validate_ids(self) -> "NeutralAnswer":
        ids = [claim.id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim ids must be unique")
        return self


class RenderedPassage(BaseModel):
    """Character-composed passage and the grounded claims supporting it."""

    text: str
    claim_ids: list[str] = Field(min_length=1)


class CharacterIncorporationError(RuntimeError):
    """Raised when a required configured character renderer cannot honor its contract."""


CHARACTER_RENDERER_SYSTEM_PROMPT = """You are a grounded character-response composer.

INPUT CONTRACT:
- original_query: the user's exact question or conversational message.
- target_language: normalized BCP-47 language tag, or "und".
- agent: optional name, description, and writing_style strings.
- claims: a non-empty array of objects with an `id` and citation-free `text`.

Answer the original user directly as the supplied character and in the requested
language. When target_language is "und", infer the language only from
original_query. Do not merely paraphrase the neutral claims. Transform them into
a natural, cohesive conversation: respond appropriately to greetings, lead with
the most relevant conclusion, group related details, and use the character's
personality, vocabulary, opinions, and relationship style.

The supplied claims are authoritative. Preserve every factual claim and
user-facing uncertainty exactly in meaning, including names, numeric values,
RPG terminology, limitations, and conclusions. You may reorganize, combine, and
condense claims, and add characterful transitions or reactions, but must not add
new world facts, rules, events, conclusions, recommendations, or certainty.

Return cohesive paragraphs as rendered_passages. Associate each passage with all
claim_ids it expresses. Use every supplied claim ID exactly once across the
response. A passage may combine multiple claims, and you control passage order.
Never output citations, citation syntax, source IDs, evidence IDs, implementation
commentary, or Markdown fences. The backend restores citations.

Return only:
{"rendered_passages":[{"text":"one cohesive character response paragraph","claim_ids":["claim-1"]}]}"""


def neutral_answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "text": {"type": "string", "minLength": 1},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "required": ["id", "text", "citations"],
                },
            },
            "uncertainty": {"type": ["string", "null"]},
        },
        "required": ["claims", "uncertainty"],
    }


def _renderer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rendered_passages": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "claim_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "required": ["text", "claim_ids"],
                },
            }
        },
        "required": ["rendered_passages"],
    }


def _model_configured(model: LLMModelTarget | str | None) -> bool:
    if isinstance(model, LLMModelTarget):
        return bool(model.provider.strip() and model.name.strip())
    return bool(isinstance(model, str) and model.strip())


def _parse_rendered_passages(
    raw: str, answer: NeutralAnswer
) -> list[RenderedPassage] | None:
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    rows = parsed.get("rendered_passages") if isinstance(parsed, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    expected = {claim.id for claim in answer.claims}
    observed: set[str] = set()
    rendered: list[RenderedPassage] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        text = str(row.get("text") or "").strip()
        raw_claim_ids = row.get("claim_ids")
        if (
            not text
            or _CITATION_MARKUP.search(text)
            or not isinstance(raw_claim_ids, list)
            or not raw_claim_ids
        ):
            return None
        claim_ids = [str(claim_id).strip() for claim_id in raw_claim_ids]
        if (
            any(not claim_id for claim_id in claim_ids)
            or len(claim_ids) != len(set(claim_ids))
            or any(claim_id not in expected for claim_id in claim_ids)
            or observed.intersection(claim_ids)
        ):
            return None
        observed.update(claim_ids)
        rendered.append(RenderedPassage(text=text, claim_ids=claim_ids))
    return rendered if observed == expected else None


async def incorporate_character(
    *,
    llm_client: Any,
    model: LLMModelTarget | str | None,
    original_query: str,
    target_language: str,
    agent_name: str | None,
    agent_description: str | None,
    writing_style: str | None,
    answer: NeutralAnswer,
    usage_tag: str,
    renderer_name: str,
    required: bool = False,
    repair_model: LLMModelTarget | str | None = None,
) -> list[RenderedPassage] | None:
    """Compose grounded passages, repairing invalid structured output once."""
    if not _model_configured(model):
        return None
    agent = {
        key: value
        for key, value in {
            "name": str(agent_name or "").strip(),
            "description": str(agent_description or "").strip(),
            "writing_style": str(writing_style or "").strip(),
        }.items()
        if value
    }
    payload = {
        "original_query": original_query,
        "target_language": normalize_target_language(target_language),
        "agent": agent,
        "claims": [
            {"id": claim.id, "text": claim.text} for claim in answer.claims
        ],
    }
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": CHARACTER_RENDERER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "usage_tag": usage_tag,
    }
    try:
        try:
            raw = str(await llm_client.chat(
                **kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": renderer_name,
                        "strict": True,
                        "schema": _renderer_schema(),
                    },
                },
            ))
        except TypeError:
            raw = str(await llm_client.chat(**kwargs))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            raw = str(await llm_client.chat(**kwargs))
        rendered = _parse_rendered_passages(raw, answer)
        if rendered is not None:
            return rendered
        if not required and not _model_configured(repair_model):
            return None

        repair_messages = [
            *kwargs["messages"],
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your previous response did not match the required renderer contract. "
                    "Return only JSON with exactly this shape: "
                    '{"rendered_passages":[{"text":"a cohesive paragraph in character",'
                    '"claim_ids":["claim-1","claim-2"]}]}. '
                    "Use every supplied claim ID exactly once across all passages. "
                    "Do not return original_query, target_language, agent, claims, citations, "
                    "commentary, or Markdown fences."
                ),
            },
        ]
        repair_kwargs = {
            **kwargs,
            "model": repair_model if _model_configured(repair_model) else model,
            "messages": repair_messages,
            "temperature": 0.1,
            "usage_tag": f"{usage_tag}.repair",
        }
        try:
            repaired_raw = str(await llm_client.chat(
                **repair_kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": renderer_name,
                        "strict": True,
                        "schema": _renderer_schema(),
                    },
                },
            ))
        except TypeError:
            repaired_raw = str(await llm_client.chat(**repair_kwargs))
        repaired = _parse_rendered_passages(repaired_raw, answer)
        if repaired is None:
            if required:
                raise CharacterIncorporationError(
                    "configured character model returned invalid output twice"
                )
            return None
        return repaired
    except CharacterIncorporationError:
        raise
    except Exception as exc:
        if required:
            raise CharacterIncorporationError(
                "configured character model call failed"
            ) from exc
        return None


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def cited_ids(
    answer: NeutralAnswer,
    *,
    rendered: list[RenderedPassage] | None,
) -> set[str]:
    """Return citation IDs used by the complete rendered answer."""
    claim_by_id = {claim.id: claim for claim in answer.claims}
    passages = rendered or [
        RenderedPassage(text=claim.text, claim_ids=[claim.id])
        for claim in answer.claims
    ]
    return {
        citation
        for passage in passages
        for claim_id in passage.claim_ids
        for citation in claim_by_id[claim_id].citations
    }


def render_answer(
    answer: NeutralAnswer,
    *,
    rendered: list[RenderedPassage] | None,
    citation_order: list[str],
) -> str:
    """Render cohesive passages with deterministic superscript source markers."""
    claim_by_id = {claim.id: claim for claim in answer.claims}
    citation_indexes: dict[str, int] = {}
    for index, citation_id in enumerate(citation_order, start=1):
        citation_indexes.setdefault(citation_id, index)
    passages = rendered or [
        RenderedPassage(text=claim.text, claim_ids=[claim.id])
        for claim in answer.claims
    ]
    output: list[str] = []
    for passage in passages:
        passage_citations = {
            citation
            for claim_id in passage.claim_ids
            for citation in claim_by_id[claim_id].citations
        }
        markers = [
            str(index).translate(_SUPERSCRIPT_DIGITS)
            for citation_id, index in citation_indexes.items()
            if citation_id in passage_citations
        ]
        suffix = f" {' '.join(markers)}" if markers else ""
        output.append(f"{passage.text.strip()}{suffix}")
    return "\n\n".join(output).strip()
