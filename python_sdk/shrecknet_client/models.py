from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class User(BaseModel):
    id: int
    username: str
    full_name: str
    email: str
    timezone: str
    role: str
    avatar_url: str | None = None
    entity_ids: list[int] = Field(default_factory=list)


class World(BaseModel):
    id: str
    name: str
    ontology_ids: list[int] = Field(default_factory=list)


class CharacterQueryResponseFormat(BaseModel):
    type: str = "text"
    schema_: dict[str, Any] | None = Field(None, alias="schema")

    model_config = {"populate_by_name": True}


class CharacterQueryGeneration(BaseModel):
    temperature: float = 0.7


class CharacterAgentQueryRequest(BaseModel):
    query: str
    use_character_identity: bool = True
    system_instruction: str | None = None
    context: dict[str, Any] | None = None
    response_format: CharacterQueryResponseFormat = Field(default_factory=CharacterQueryResponseFormat)
    generation: CharacterQueryGeneration = Field(default_factory=CharacterQueryGeneration)


class CharacterAgentQueryResult(BaseModel):
    type: str
    content: Any
    decision_basis: str


class CharacterAgentQueryQueued(BaseModel):
    job_id: int
    status: str
    stage: str
    progress: float
    status_url: str


class CharacterAgentQueryError(BaseModel):
    code: str
    message: str


class CharacterAgentQueryJobRead(BaseModel):
    job_id: int
    character_agent_id: str
    status: str
    stage: str
    progress: float
    result: CharacterAgentQueryResult | None = None
    error: CharacterAgentQueryError | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class EmbodimentDraftCreate(BaseModel):
    ontology_id: int
    entity_instance_id: str


class EmbodimentDraftStart(BaseModel):
    draft_id: str
    job_id: int
    status: str
    draft_url: str
    job_url: str


class EmbodimentDraftRead(BaseModel):
    id: str
    ontology_id: int
    source_entity_id: str
    target_character_agent_id: str | None = None
    status: str
    background_job_id: int | None = None
    generation_revision: int
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    observations: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    timeline: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    error_message: str | None = None
    generated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CharacterAgentEmbeddedAspect(BaseModel):
    suggestion_id: str | None = None
    name: str
    category: str
    description: str | None = None
    importance: int
    intensity: int | None = None
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class CharacterAgentEmbeddedGoal(BaseModel):
    suggestion_id: str | None = None
    title: str
    description: str | None = None
    goal_type: str
    status: str = "active"
    priority: int = 50
    commitment: int = 50
    justification: str | None = None
    basis: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class CharacterAgentCreateRequest(BaseModel):
    ontology_id: int
    entity_instance_id: str
    embodiment_draft_id: str | None = None
    name: str | None = None
    subtitle: str | None = None
    background_story: str | None = None
    image_url: str | None = None
    status: str = "active"
    visibility: str = "private"
    calm_aggressive: int = 50
    cautious_reckless: int = 50
    compassionate_ruthless: int = 50
    trusting_suspicious: int = 50
    honest_deceptive: int = 50
    patient_impulsive: int = 50
    humble_proud: int = 50
    cooperative_dominating: int = 50
    trait_adherence: int = 80
    aspects: list[CharacterAgentEmbeddedAspect] = Field(default_factory=list)
    goals: list[CharacterAgentEmbeddedGoal] = Field(default_factory=list)


class CharacterAgentRead(BaseModel):
    id: str
    ontology_id: int
    entity_instance_id: str
    embodied_entity_instance_id: str
    name: str
    subtitle: str | None = None
    background_story: str
    image_url: str | None = None
    status: str
    visibility: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"extra": "allow"}


class CharacterAgentUpdate(BaseModel):
    name: str | None = None
    subtitle: str | None = None
    background_story: str | None = None
    image_url: str | None = None
    status: str | None = None
    visibility: str | None = None
    calm_aggressive: int | None = None
    cautious_reckless: int | None = None
    compassionate_ruthless: int | None = None
    trusting_suspicious: int | None = None
    honest_deceptive: int | None = None
    patient_impulsive: int | None = None
    humble_proud: int | None = None
    cooperative_dominating: int | None = None
    trait_adherence: int | None = None


class CharacterIdentityRevisionRead(BaseModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    last_processed_scene_id: str | None = None
    name: str
    subtitle: str | None = None
    trait_adherence: int
    behavioural_axes: dict[str, int]
    active_aspect_ids: list[str] = Field(default_factory=list)
    active_goal_ids: list[str] = Field(default_factory=list)
    provenance_type: str
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime


class CharacterIdentityChangeRead(BaseModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    change_type: str
    field_name: str
    previous_value: Any = None
    new_value: Any = None
    confidence: float | None = None
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_type: str
    created_at: datetime


class ScenePerspectiveCreate(BaseModel):
    scene_id: str
    source_type: str
    awareness_level: int
    confidence: int
    summary: str
    interpretation: str
    memory_strength: int
    importance: int
    status: str = "active"


class ScenePerspectiveUpdate(BaseModel):
    source_type: str | None = None
    awareness_level: int | None = None
    confidence: int | None = None
    summary: str | None = None
    interpretation: str | None = None
    memory_strength: int | None = None
    importance: int | None = None
    status: str | None = None


class EmotionalInterpretationCreate(BaseModel):
    arousal: int
    valence: int
    description: str


class EmotionalInterpretationUpdate(BaseModel):
    arousal: int | None = None
    valence: int | None = None
    description: str | None = None


class EmotionalInterpretationRead(EmotionalInterpretationCreate):
    id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime


class CharacterBeliefCreate(BaseModel):
    statement: str
    confidence: int
    status: str


class CharacterBeliefUpdate(BaseModel):
    statement: str | None = None
    confidence: int | None = None
    status: str | None = None


class CharacterBeliefRead(CharacterBeliefCreate):
    id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime


class CharacterImpactCreate(BaseModel):
    impact_type: str
    direction: str
    magnitude: int
    description: str
    target_id: str
    caused_by_milestone_id: str | None = None


class CharacterImpactUpdate(BaseModel):
    direction: str | None = None
    magnitude: int | None = None
    description: str | None = None
    caused_by_milestone_id: str | None = None


class CharacterImpactRead(BaseModel):
    id: str
    ontology_id: int
    impact_type: str
    direction: str
    magnitude: int
    description: str
    target_id: str
    target_type: str
    caused_by_milestone_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ScenePerspectiveRead(BaseModel):
    id: str
    ontology_id: int
    character_agent_id: str
    scene_id: str
    generated_with_revision_id: str | None = None
    source_group_id: str | None = None
    source_type: str
    awareness_level: int
    confidence: int
    summary: str
    interpretation: str
    memory_strength: int
    importance: int
    status: str
    created_at: datetime
    updated_at: datetime


class ScenePerspectiveAggregateRead(ScenePerspectiveRead):
    emotions: list[EmotionalInterpretationRead] = Field(default_factory=list)
    beliefs: list[CharacterBeliefRead] = Field(default_factory=list)
    impacts: list[CharacterImpactRead] = Field(default_factory=list)


class Ontology(BaseModel):
    id: int
    name: str
    description: str | None = None
    rpg_system: str | None = None
    image_url: str | None = None
    created_at: datetime
    updated_at: datetime


class OntologyWorldStatsItem(BaseModel):
    ontology_id: int
    entity_type_count: int = 0
    entity_instance_count: int = 0
    library_item_count: int = 0
    scene_count: int = 0
    milestone_count: int = 0
    updated_at: datetime


class OntologyWorldStatsResponse(BaseModel):
    results: list[OntologyWorldStatsItem] = Field(default_factory=list)


class OntologyInstanceCount(BaseModel):
    total: int


class OntologyInstanceEntity(BaseModel):
    entity_instance_id: str | None = None
    definition_id: int
    alias: str
    text: str | None = None
    author_type: str
    author_id: str
    properties: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceScene(BaseModel):
    id: str | None = None
    name: str
    description: str
    created_by_type: str
    created_by_author: str
    derived_from: dict[str, Any]
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    relates_to: list[dict[str, Any]] = Field(default_factory=list)
    local_order: dict[str, Any] = Field(default_factory=dict)


class OntologyInstance(BaseModel):
    id: str
    ontology_id: int
    name: str
    entities: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceCreate(BaseModel):
    ontology_id: int
    name: str
    entities: list[OntologyInstanceEntity] = Field(default_factory=list)
    scenes: list[OntologyInstanceScene] = Field(default_factory=list)


class OntologyInstanceUpdate(BaseModel):
    name: str | None = None
    entities: list[OntologyInstanceEntity] | None = None
    scenes: list[OntologyInstanceScene] | None = None


class OntologyInstanceSummaryPage(BaseModel):
    total: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceSearchResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)


class OntologyEntityResolveResponse(BaseModel):
    ontology_id: int
    results: list[dict[str, Any]] = Field(default_factory=list)
    missing_entity_instance_ids: list[str] = Field(default_factory=list)


class OntologyInstanceSceneCountsResponse(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


class UserBootstrapStatus(BaseModel):
    has_users: bool


class AgentBase(BaseModel):
    name: str
    avatar_url: str | None = None
    description: str | None = None
    writing_style: str | None = None
    job: str = "elder"
    active: bool = True


class AgentCreate(AgentBase):
    ontology_ids: list[int] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    writing_style: str | None = None
    job: str | None = None
    active: bool | None = None


class AgentRead(AgentBase):
    id: str
    created_at: datetime
    updated_at: datetime
    ontology_ids: list[int] = Field(default_factory=list)


class PersonalCompanionAgentBase(BaseModel):
    name: str
    avatar_url: str | None = None
    writing_style: str
    active: bool = True


class PersonalCompanionAgentCreate(PersonalCompanionAgentBase):
    pass


class PersonalCompanionAgentUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None
    writing_style: str | None = None
    active: bool | None = None


class PersonalCompanionAgentRead(PersonalCompanionAgentBase):
    id: str
    user_id: int
    created_at: datetime
    updated_at: datetime


class ProviderValidation(BaseModel):
    configured: bool | None = None
    present: bool | None = None
    active: bool | None = None
    valid: bool | None = None
    error: str | None = None


class ProviderStatus(BaseModel):
    provider_id: str
    enabled: bool
    active: bool | None = None
    valid: bool | None = None
    configured: bool | None = None
    error: str | None = None
    models: list[str] = Field(default_factory=list)


class LLMReadinessReport(BaseModel):
    checks: dict[str, bool] = Field(default_factory=dict)
    providers: list[ProviderStatus] = Field(default_factory=list)
    ready: bool
    reasons: list[str] = Field(default_factory=list)


class BackgroundJobRecord(BaseModel):
    id: int
    job_type: str
    status: str
    author_type: str | None = None
    author_id: str | None = None
    ontology_id: int | None = None
    description: str = ""
    details: dict[str, Any] | str | None = None
    progress: float = 0.0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in {"done", "failed"}

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class EmbeddingStats(BaseModel):
    ontology_id: int
    total_nodes: int
    embedded_nodes: int
    unembedded_nodes: int
    outdated_nodes: int
    entities: dict[str, int]
    scenes: dict[str, int]
    milestones: dict[str, int]


class EmbeddingTriggerResponse(BaseModel):
    job_id: str
    ontology_id: int
    message: str
    requested_entities: int
    requested_scenes: int
    requested_milestones: int


class GraphRAGEmbedNodeResult(BaseModel):
    node_id: str
    context_text: str
    embedding_model: str
    embedding_dim: int


class GraphRAGEmbedOntologyResult(BaseModel):
    ontology_id: int
    nodes_processed: int
    nodes_failed: int


class GraphRAGIndexStatus(BaseModel):
    index_name: str
    exists: bool
    embedding_model: str
    embedding_dim: int


class GraphRAGResetEmbeddingsResult(BaseModel):
    ontology_id: int
    nodes_reset: int
    orphans_deleted: int
    chunks_deleted: int


class EmbeddingLifecycleReport(BaseModel):
    ontology_id: int
    stats_available: bool
    total_nodes: int = 0
    embedded_nodes: int = 0
    unembedded_nodes: int = 0
    outdated_nodes: int = 0
    entities: dict[str, int] = Field(default_factory=dict)
    scenes: dict[str, int] = Field(default_factory=dict)
    milestones: dict[str, int] = Field(default_factory=dict)


class ElderQueryRequest(BaseModel):
    query: str
    mode: str = "both"
    top_k: int | None = None
    include_trace: bool = False
    fast: bool = False
    route: str = "auto"
    chat_id: str | None = None
    entities_hint: str | None = None
    node_scope: str = "everything"
    candidate_limit: int | None = 120
    rerank_limit: int | None = 50


class ElderSourceEvidenceChunk(BaseModel):
    chunk_id: str | None = None
    chunk_type: str | None = None
    score: float = 0.0
    text: str | None = None
    complete: bool = True


class ElderSourceNode(BaseModel):
    node_id: str
    node_label: str | None = None
    node_name: str | None = None
    node_type: str | None = None
    scene_id: str | None = None
    source_entity_instance_id: str | None = None
    score: float = 0.0
    evidence_chunks: list[ElderSourceEvidenceChunk] = Field(default_factory=list)
    evidence_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    temporal_position: dict[str, Any] = Field(default_factory=dict)
    retrieval_methods: list[str] = Field(default_factory=list)
    complete: bool = True
    canonical_text: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class ElderRetrievalPlanStep(BaseModel):
    id: str
    purpose: str
    operation: str
    query: str | None = None
    inputs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    temporal: dict[str, Any] = Field(default_factory=dict)
    traversal: dict[str, Any] = Field(default_factory=dict)
    target_data_type: str
    limit: int
    hydration_mode: str
    context_chunks_before: int
    context_chunks_after: int
    max_tokens_per_source: int


class ElderRetrievalPlan(BaseModel):
    answer_goal: str
    response_scope: str
    evidence_budget_tokens: int
    query_intent: dict[str, Any] = Field(default_factory=dict)
    steps: list[ElderRetrievalPlanStep] = Field(default_factory=list)


class ElderQueryResponse(BaseModel):
    agent_id: str
    query: str
    answer: str
    timings: dict[str, float] = Field(default_factory=dict)
    retrieval_plan: ElderRetrievalPlan
    sources: list[ElderSourceNode] = Field(default_factory=list)
    memory_priors_applied: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str
    trace: list[dict[str, Any]] | None = None
    retrieval_debug: list[dict[str, Any]] | None = None
    pipeline_version: str = "elder-query-retrieval-v2"
    llm_usage: list[dict[str, Any]] = Field(default_factory=list)
    llm_usage_totals: dict[str, int] = Field(default_factory=dict)


class ElderChatCreate(BaseModel):
    agent_id: str
    name: str
    color: str | None = None


class ElderChatUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class ElderChatHistoryEntry(BaseModel):
    id: int
    chat_id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] | None = None


class ElderChatRead(BaseModel):
    id: str
    user_id: int
    agent_id: str
    name: str
    color: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int | None = None


class ElderChatWithHistory(ElderChatRead):
    history: list[ElderChatHistoryEntry] = Field(default_factory=list)


class ElderChatsList(BaseModel):
    chats: list[ElderChatRead] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ElderPreflightReport(BaseModel):
    ready: bool
    reasons: list[str] = Field(default_factory=list)
    llm_ready: bool
    agent_ready: bool
    embedding_ready: bool
    provider_checks: dict[str, bool] = Field(default_factory=dict)


class ArchitectAnalysisRequest(BaseModel):
    ontology_instance_id: str
    ontology_id: int | None = None
    max_chunks: int | None = None
    chunk_size: int | None = None


class ArchitectProposalRead(BaseModel):
    id: str
    proposal_type: str
    status: str
    entity_definition_id: int | None = None
    entity_instance_id: str | None = None
    alias: str | None = None
    confidence: float | None = None
    justification: str | None = None
    evidence: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    chunks: list[str] | None = None
    merged_into_proposal_id: str | None = None
    corrected_alias: str | None = None
    corrected_entity_definition_id: int | None = None
    corrected_proposal_type: str | None = None
    corrected_entity_instance_id: str | None = None
    generated_entity_instance_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ArchitectRunRead(BaseModel):
    id: str
    agent_id: str | None = None
    background_job_id: int | None = None
    generation_job_id: int | None = None
    ontology_id: int | None = None
    ontology_instance_id: str
    status: str
    input_chunk_count: int | None = None
    settings: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    proposals: list[ArchitectProposalRead] = Field(default_factory=list)


class ArchitectRunSummary(BaseModel):
    id: str
    agent_id: str | None = None
    background_job_id: int | None = None
    generation_job_id: int | None = None
    ontology_id: int | None = None
    ontology_instance_id: str
    status: str
    input_chunk_count: int | None = None
    created_at: datetime
    updated_at: datetime


class ArchitectProposalCreate(BaseModel):
    proposal_type: str
    status: str
    entity_definition_id: int | None = None
    entity_instance_id: str | None = None
    alias: str | None = None
    confidence: float | None = None
    justification: str | None = None
    evidence: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    chunks: list[str] | None = None


class ArchitectProposalUpdate(BaseModel):
    proposal_type: str | None = None
    status: str | None = None
    entity_definition_id: int | None = None
    entity_instance_id: str | None = None
    alias: str | None = None
    confidence: float | None = None
    justification: str | None = None
    evidence: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    chunks: list[str] | None = None
    merged_into_proposal_id: str | None = None
    corrected_alias: str | None = None
    corrected_entity_definition_id: int | None = None
    corrected_proposal_type: str | None = None
    corrected_entity_instance_id: str | None = None
    generated_entity_instance_id: str | None = None


class ArchitectProposalStatusUpdate(BaseModel):
    proposal_ids: list[str]
    status: str


class ArchitectGenerationRequest(BaseModel):
    run_id: str
    reviewed_pipeline_output: dict[str, Any]
    author_type: str = "user"
    author_id: str


class ArchitectPreflightReport(BaseModel):
    ready: bool
    reasons: list[str] = Field(default_factory=list)
    llm_ready: bool
    agent_ready: bool
    provider_checks: dict[str, bool] = Field(default_factory=dict)


class NovelistRunCreate(BaseModel):
    unstructured_text: str
    language: str | None = None
    instructions: str | None = None
    previous_session_id: str | None = None
    previous_session_text: str | None = None
    previous_session_summary: str | None = None


class NovelistRunRead(BaseModel):
    id: str
    agent_id: str
    background_job_id: int | None = None
    ontology_id: int | None = None
    ontology_instance_id: str | None = None
    status: str
    stage: str
    settings: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    previous_session_id: str | None = None
    previous_session_summary: str | None = None
    previous_session_lookup_status: str | None = None
    elder_qna_by_part: dict[str, dict[str, list[str]]] | None = None
    scene_results: list[dict[str, Any]] | None = None
    step_outputs: dict[str, Any] | None = None
    timing_summary: dict[str, Any] | None = None
    stage_timings: dict[str, float] | None = None
    scene_progress: dict[str, dict[str, Any]] | None = None
    draft_text: str | None = None
    critic_notes: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
