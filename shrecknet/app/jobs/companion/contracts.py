"""Strict model outputs and deterministic reference/evidence validation."""
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from app.schemas.character_companion import DIMENSIONS


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^s(?:[1-9]|[12][0-9]|30)$")
    kind: Literal["conversation", "fact", "subjective_memory", "opinion", "speculation", "uncertainty"]
    text: str = Field(min_length=1, max_length=16000)
    evidence_ids: list[str] = Field(max_length=20)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[Segment] = Field(min_length=1, max_length=30)
    coverage: Literal["complete", "partial", "unknown"]
    warnings: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({row.id for row in self.segments}) != len(self.segments):
            raise ValueError("Duplicate segment IDs")
        return self

    def final(self, evidence, tool_results):
        by_id = {item["evidence_id"]: item for item in evidence}
        citations = []
        for segment in self.segments:
            if segment.kind in {"fact", "subjective_memory"} and not segment.evidence_ids:
                raise ValueError("Missing evidence")
            for identifier in segment.evidence_ids:
                if identifier not in by_id:
                    raise ValueError("Unknown evidence ID")
                item = by_id[identifier]
                if segment.kind == "subjective_memory" and item["source_type"] != "character_memory":
                    raise ValueError("Objective source cannot be a subjective memory")
                if segment.kind == "fact" and item["source_type"] == "character_memory":
                    raise ValueError("Subjective memory cannot establish objective fact")
                citations.append({"citation_id": f"c{len(citations)+1}", "segment_ids": [segment.id], **item})
        partial = any(row["status"] not in {"ok", "empty"} for row in tool_results)
        return {"text": "\n\n".join(row.text for row in self.segments), "segments": [row.model_dump() for row in self.segments],
                "citations": citations, "evidence": evidence, "coverage": "partial" if partial else self.coverage,
                "warnings": self.warnings + [f"{row['tool']}: {row['status']}" for row in tool_results if row["status"] != "ok"]}


def provisional_text(raw):
    """Project only complete/partial JSON text strings; never stream scaffolding."""
    parts = []
    for match in re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)', raw):
        value = match.group(1)
        try:
            parts.append(json.loads('"' + value + '"'))
        except ValueError:
            pass  # Split escape/unicode sequence: wait for another provider chunk.
    return "\n\n".join(parts)


class RapportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    quote: str = Field(min_length=1, max_length=1000)


class RapportChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal["connection", "coordination", "trust", "familiarity", "openness", "current_strain"]
    delta: StrictInt
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: list[RapportEvidence] = Field(max_length=10)
    reason: str = Field(max_length=1000)


class RapportProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: list[RapportChange] = Field(max_length=6)

    @model_validator(mode="after")
    def unique(self):
        if len({row.dimension for row in self.changes}) != len(self.changes):
            raise ValueError("Duplicate dimension")
        return self

    def apply(self, scores, messages):
        result = dict(scores)
        by_id = {message["id"]: message["content"] for message in messages}
        for row in self.changes:
            valid = bool(row.evidence) and all(item.message_id in by_id and item.quote in by_id[item.message_id] for item in row.evidence)
            if row.confidence < 0.6 or not valid:
                continue
            bound = 5 if row.dimension == "current_strain" else 2
            result[row.dimension] = max(0, min(100, scores[row.dimension] + max(-bound, min(bound, row.delta))))
        return result
