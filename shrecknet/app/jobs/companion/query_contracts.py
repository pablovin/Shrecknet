"""Strict internal contracts for scope selection and bounded source plans."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from app.jobs.elder.v2_schemas import RetrievalPlan


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


Source = Literal['atlas', 'shreckworld', 'chronicle', 'character_memory', 'web']


class ResearchTask(Contract):
    query: QueryText
    sources: list[Source] = Field(min_length=1, max_length=5)


class SimpleCompanionPlan(Contract):
    mode: Literal['search', 'chat', 'clarify']
    tasks: list[ResearchTask] = Field(max_length=3)
    clarification_questions: list[QueryText] = Field(max_length=3)

    @model_validator(mode='after')
    def consistent(self):
        if ((self.mode == 'search') != bool(self.tasks) or
                (self.mode == 'clarify') != bool(self.clarification_questions)):
            raise ValueError('inconsistent_router_mode')
        return self


class ScopeIntent(Contract):
    action: Literal['keep', 'switch', 'clarify']
    ontology_name: str | None = Field(None, min_length=1, max_length=255)
    resume_pending: bool

    @model_validator(mode='after')
    def consistent(self):
        if (self.action == 'switch') != (self.ontology_name is not None):
            raise ValueError('switch_requires_target_only')
        return self


class OntologyDecision(Contract):
    resume_pending: bool = False
    status: Literal['resolved', 'ambiguous', 'unresolved', 'not_needed']
    ontology_id: int | None = None
    ontology_name: str | None = Field(None, max_length=255)
    reason: str = Field(max_length=500)


class Need(Contract):
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=17000)


class PlannedCall(Contract):
    id: str = Field(min_length=1, max_length=40)
    tool: Literal['atlas', 'shreckworld', 'web', 'chronicle', 'character_memory']
    query: str = Field(min_length=1, max_length=4000)
    need_ids: list[str] = Field(min_length=1, max_length=6)
    retrieval_depth: Literal['light', 'deep'] = 'light'
    depth_reason: str | None = Field(None, min_length=1, max_length=500)
    atlas_plan: RetrievalPlan | None = None
    memory_reason: str | None = Field(None, min_length=1, max_length=500)

    @model_validator(mode='after')
    def tool_options(self):
        if self.retrieval_depth == 'deep' and (self.tool not in {'atlas', 'shreckworld'} or not self.depth_reason or not self.depth_reason.strip()):
            raise ValueError('deep_retrieval_requires_reason')
        if self.retrieval_depth == 'light' and self.depth_reason is not None:
            raise ValueError('light_retrieval_has_no_depth_reason')
        if self.atlas_plan is not None and self.tool != 'atlas':
            raise ValueError('atlas_plan_requires_atlas')
        if self.tool == 'character_memory':
            if not self.memory_reason or not self.memory_reason.strip():
                raise ValueError('memory_retrieval_requires_reason')
        elif self.memory_reason is not None:
            raise ValueError('memory_reason_requires_character_memory')
        return self


class OntologyUpdate(Contract):
    ontology_name: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)


class QueryPlan(Contract):
    action: Literal['retrieve', 'ontology_update', 'clarify', 'conversation']
    ontology_update: OntologyUpdate | None
    needs: list[Need] = Field(max_length=6)
    calls: list[PlannedCall] = Field(max_length=9)
    clarification_questions: list[str] = Field(max_length=3)
    resume_pending: bool = False

    @model_validator(mode='after')
    def consistent(self):
        ids = {n.id for n in self.needs}
        if len(ids) != len(self.needs) or len({c.id for c in self.calls}) != len(self.calls):
            raise ValueError('duplicate_plan_ids')
        if any(not set(c.need_ids) <= ids for c in self.calls):
            raise ValueError('unknown_need')
        if self.action == 'retrieve':
            if not self.needs or self.ontology_update or self.clarification_questions:
                raise ValueError('invalid_retrieval_action')
        elif self.action == 'ontology_update':
            if not self.ontology_update or self.calls or self.needs or self.clarification_questions:
                raise ValueError('invalid_update_action')
        elif self.action == 'clarify':
            if not self.clarification_questions or any(not q.strip() or len(q) > 500 for q in self.clarification_questions) or self.calls or self.needs or self.ontology_update:
                raise ValueError('invalid_clarification_action')
        elif self.calls or self.needs or self.ontology_update or self.clarification_questions:
            raise ValueError('invalid_conversation_action')
        return self
