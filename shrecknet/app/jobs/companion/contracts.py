"""Deterministic answer construction and strict rapport proposal validation."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from app.schemas.character_companion import DIMENSIONS
from app.jobs.companion.errors import CompanionExecutionError


MARKER_PREFIX = "[[source:"
MARKER = re.compile(r"\[\[source:([^\]\r\n]*)\]\]")


def provisional_text(raw, *, complete=False):
    """Hide source markers, including prefixes split across streaming chunks."""
    if not complete:
        start = raw.rfind(MARKER_PREFIX)
        if start != -1 and "]]" not in raw[start:]:
            raw = raw[:start]
        else:
            for size in range(len(MARKER_PREFIX) - 1, 0, -1):
                if raw.endswith(MARKER_PREFIX[:size]):
                    raw = raw[:-size]
                    break
    return MARKER.sub("", raw)


def build_answer(raw, evidence, tool_results, *, query=None, citation_evidence=None):
    """Resolve aliases against exactly the records supplied to the model.

    Unknown/malformed markers are rejected as citations, never fabricated links.
    The complete evidence pool remains available for diagnostics and access checks.
    This validates attribution identifiers, not factual support or entailment.
    """
    text = provisional_text(raw, complete=True)
    malformed = MARKER_PREFIX in text
    # A partial marker is not readable prose and must not leak into final text.
    text = re.sub(r"\[\[source:[^\]\r\n]*(?:\]\]?|(?=[\r\n]|$))", "", text)
    if not text.strip():
        raise CompanionExecutionError("empty_synthesis")
    aliases = {str(index): item for index, item in enumerate(
        evidence if citation_evidence is None else citation_evidence, 1)}
    cited = []
    invalid_count = 0
    valid_count = 0
    for match in MARKER.finditer(raw):
        item = aliases.get(match.group(1))
        if item is None:
            invalid_count += 1
        else:
            valid_count += 1
            if item["evidence_id"] not in {source["evidence_id"] for source in cited}:
                cited.append(item)
    citations = [{"citation_id": f"c{index}", "segment_ids": ["s1"], **item}
                 for index, item in enumerate(cited, 1)]
    supplied = {item["evidence_id"] for item in evidence}
    tool_calls = []
    for row in tool_results:
        returned = row.get("evidence", [])
        call = {
            "tool": row["tool"], "status": row["status"], "query": row.get("query", query),
            "cache_hit": row.get("cache_hit", False), "elapsed_ms": row.get("elapsed_ms"),
            "returned_count": len(returned),
            "evidence_ids": [item["evidence_id"] for item in returned if item["evidence_id"] in supplied],
        }
        for key in ("reason", "source_coverage", "id", "need_ids", "recovery", "source_trace",
                    "retrieval_depth", "timeout_s", "timeout_layer", 'source_readiness',
                    'sources_requiring_attention', 'evidence_omissions'):
            if key in row:
                call[key] = row[key]
        tool_calls.append(call)
    snapshot_partial = any(row["source_type"] == "foundry" and row["coverage"]["status"] != "complete" for row in evidence)
    partial = snapshot_partial or any(row["status"] not in {"ok", "empty"} or row.get('source_readiness')=='partial' for row in tool_results)
    warnings = (["Foundry snapshot coverage is partial; missing game state remains unknown."] if snapshot_partial else [])
    warnings += [f"{row['tool']}: {row.get('reason', row['status'])}" for row in tool_results if row["status"] != "ok" or row.get('reason')]
    warnings += [f"shreckworld: {item['title']} ({item['library_item_id']}): {item['reason']}"
                 for row in tool_results for item in row.get('sources_requiring_attention', [])]
    if invalid_count:
        warnings.append("The response contained an unknown source marker; it was not cited.")
    if malformed:
        warnings.append("The response contained an incomplete source marker.")
    return {"text": text, "segments": [{"id": "s1", "kind": "conversation", "text": text,
                                       "evidence_ids": [item["evidence_id"] for item in cited]}],
            "citations": citations, "evidence": evidence, "tool_calls": tool_calls,
            "coverage": "partial" if partial or invalid_count or malformed else "unknown", "warnings": warnings,
            "citation_validation": {"valid": valid_count, "unknown": invalid_count,
                                    "malformed": malformed}}


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
