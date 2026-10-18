"""Typed contracts for ShreckWorld's planned evidence-retrieval pipeline."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NeedKind = Literal["definition", "mechanic", "procedure", "condition", "comparison", "other"]
NeedCoverageStatus = Literal["covered", "partial", "missing", "unresolved"]


class QueryNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^need-[1-6]$")
    question: str = Field(min_length=1, max_length=1000)
    kind: NeedKind = "other"
    required: bool = True
    coverage_requirement: str = Field(min_length=1, max_length=500)
    priority: int = Field(ge=1, le=6)

    @field_validator("question", "coverage_requirement")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query need text must not be blank")
        return value


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: Literal["query-v3.1"] = "query-v3.1"
    intent: Literal["simple", "multi_rule", "comparison", "exhaustive"]
    requires_exhaustive_coverage: bool = False
    needs: list[QueryNeed] = Field(min_length=1, max_length=6)

    @field_validator("needs")
    @classmethod
    def unique_need_ids(cls, needs: list[QueryNeed]) -> list[QueryNeed]:
        ids = [need.id for need in needs]
        if len(set(ids)) != len(ids):
            raise ValueError("Query need IDs must be unique")
        return needs

    @model_validator(mode="after")
    def validate_exhaustive_intent(self) -> "QueryPlan":
        if self.requires_exhaustive_coverage != (self.intent == "exhaustive"):
            raise ValueError("Exhaustive intent and coverage flag must agree")
        return self


class NeedCoverage(BaseModel):
    need_id: str
    status: NeedCoverageStatus
    evidence_ids: list[str] = Field(default_factory=list)
    supported_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    assessment_method: Literal["deterministic", "verified_model", "unverified"]
    explanation: str


class EvidenceCoverage(BaseModel):
    status: Literal["adequate", "partial", "unresolved"]
    needs: list[NeedCoverage]
    unresolved_questions: list[str] = Field(default_factory=list)


class EvidenceGroup(BaseModel):
    evidence_id: str
    need_ids: list[str]
    chunk_ids: list[str]
    incomplete_evidence: bool = False
    omission_reason: str | None = None


class QueryV3Trace(BaseModel):
    planning_ms: float = 0
    retrieval_ms: float = 0
    coverage_ms: float = 0
    followup_rounds: int = 0
    planner_llm_calls: int = 0
    coverage_llm_calls: int = 0
    retrieved_candidates: int = 0
    deduplicated_evidence: int = 0
    evidence_tokens: int = 0
    planner_fallback: bool = False
    qdrant_fallback: bool = False
    reranker_fallback: bool = False
