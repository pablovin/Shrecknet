from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanionDefaultStyle(BaseModel):
    verbosity: float = Field(0.6, ge=0.0, le=1.0)
    humor: float = Field(0.4, ge=0.0, le=1.0)
    directness: float = Field(0.5, ge=0.0, le=1.0)
    initiative: float = Field(0.6, ge=0.0, le=1.0)


class PersonalCompanionAgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str = Field(..., min_length=1)
    core_traits: list[str] = Field(default_factory=lambda: ["curious", "warm", "grounded"], min_length=1, max_length=12)
    archetype: str = Field("companion", min_length=1, max_length=64)
    voice: str = Field("clear and helpful", min_length=1, max_length=128)
    boundaries: list[str] = Field(
        default_factory=lambda: ["do not invent canon", "do not fake certainty"],
        max_length=12,
    )
    default_style: CompanionDefaultStyle = Field(default_factory=CompanionDefaultStyle)
    active: bool = True


class PersonalCompanionAgentCreate(PersonalCompanionAgentBase):
    pass


class PersonalCompanionAgentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str | None = Field(None, min_length=1)
    core_traits: list[str] | None = Field(None, min_length=1, max_length=12)
    archetype: str | None = Field(None, min_length=1, max_length=64)
    voice: str | None = Field(None, min_length=1, max_length=128)
    boundaries: list[str] | None = Field(None, max_length=12)
    default_style: CompanionDefaultStyle | None = None
    active: bool | None = None


class PersonalCompanionAgentRead(PersonalCompanionAgentBase):
    id: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompanionUserRapportRead(BaseModel):
    user_id: int
    companion_id: str
    adaptive_traits: dict[str, float] = Field(default_factory=dict)
    observed_preferences: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    recent_user_state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class AllocatedToolAgent(BaseModel):
    id: str
    name: str
    job: str
    ontology_ids: list[int] = Field(default_factory=list)


class OrchestratorToolAllocation(BaseModel):
    elder: list[AllocatedToolAgent] = Field(default_factory=list)
    librarian: list[AllocatedToolAgent] = Field(default_factory=list)


class CompanionWorldBootstrapRequest(BaseModel):
    ontology_id: int = Field(..., ge=1)


class CompanionWorldBootstrapResponse(BaseModel):
    companion_id: str
    ontology_id: int
    allocated_tools: OrchestratorToolAllocation
    existing_chat_count: int
    chat_limit: int


class CompanionChatSessionCreateRequest(BaseModel):
    ontology_id: int = Field(..., ge=1)
    title: str | None = Field(None, min_length=1, max_length=255)


class CompanionChatSessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class CompanionChatSessionRead(BaseModel):
    session_id: str
    companion_id: str
    ontology_id: int
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class CompanionChatSessionCount(BaseModel):
    ontology_id: int
    count: int
    limit: int


class CompanionOrchestratorTurnRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=3000)


class CompanionOrchestratorTurnQueuedResponse(BaseModel):
    job_id: int
    status: Literal["queued"]
    session_id: str
    ontology_id: int


class ProgressState(BaseModel):
    current: int
    total: int


class ToolProgressState(BaseModel):
    total: int
    completed: int
    running: int


class StepProgressState(ToolProgressState):
    current: int | None = None


class RoutingDecision(BaseModel):
    use_elder: bool
    use_librarian: bool
    reason: str


class SelectedTools(BaseModel):
    elder: list[str] = Field(default_factory=list)
    librarian: list[str] = Field(default_factory=list)


class PlanningStep(BaseModel):
    step_id: str
    tool_job: Literal["elder", "librarian"]
    goal: str
    query: str
    depends_on: list[str] = Field(default_factory=list)
    use_prior_context: bool = False
    success_requirements: list[str] = Field(default_factory=list)
    on_failure: Literal["stop"] = "stop"


class ExecutionPlan(BaseModel):
    strategy: Literal["parallel", "sequential"]
    reason: str
    steps: list[PlanningStep] = Field(default_factory=list)


class ExecutionCompletedStep(BaseModel):
    step_id: str
    tool_job: Literal["elder", "librarian"]
    goal: str
    query_used: str
    agent_id: str
    agent_name: str
    ok: bool
    answer: str


class ExecutionState(BaseModel):
    completed_steps: list[ExecutionCompletedStep] = Field(default_factory=list)
    stopped_reason: str | None = None


class AgentResponse(BaseModel):
    ok: bool
    agent_id: str
    agent_name: str
    agent_job: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class CompanionPolicyResponseStyle(BaseModel):
    directness: float
    technical_depth: float
    playfulness: float
    initiative: float


class CompanionPolicy(BaseModel):
    chat_goal: str
    turn_intention: str
    conversation_mode: str
    user_need: str
    needs_knowledge_tools: bool
    suggested_response_style: CompanionPolicyResponseStyle
    open_threads: list[str] = Field(default_factory=list)
    next_best_actions: list[str] = Field(default_factory=list)


class CompanionChatState(BaseModel):
    chat_goal: str
    conversation_mode: str
    current_intention: str
    open_threads: list[str] = Field(default_factory=list)
    next_best_actions: list[str] = Field(default_factory=list)
    recent_user_state: dict[str, Any] = Field(default_factory=dict)


class CompanionRapportProfile(BaseModel):
    adaptive_traits: dict[str, float] = Field(default_factory=dict)
    observed_preferences: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    recent_user_state: dict[str, Any] = Field(default_factory=dict)
    applied_patch: list[dict[str, Any]] = Field(default_factory=list)


class ProactivityDecision(BaseModel):
    should_be_proactive: bool
    proactivity_type: str
    proactive_message: str = ""


class ChatStatePatch(BaseModel):
    chat_goal: str = ""
    current_intention: str = ""
    open_threads_add: list[str] = Field(default_factory=list)
    open_threads_resolved: list[str] = Field(default_factory=list)
    next_best_actions: list[str] = Field(default_factory=list)


class RapportPatchItem(BaseModel):
    trait: str
    delta: float
    confidence: float
    reason: str = ""


class TurnReflection(BaseModel):
    answered_user: bool
    confidence: float
    user_state_estimate: dict[str, str] = Field(default_factory=dict)
    response_quality: dict[str, bool] = Field(default_factory=dict)
    proactivity: ProactivityDecision
    chat_state_patch: ChatStatePatch
    rapport_patch: list[RapportPatchItem] = Field(default_factory=list)


class RapportPatchApplied(BaseModel):
    trait: str
    before: float
    after: float
    delta_requested: float
    delta_applied: float
    confidence: float
    reason: str = ""


class RunningTurnPayloadBase(BaseModel):
    status: Literal["running"]
    query: str
    session_id: str
    ontology_id: int
    companion_id: str
    allocated_tools: OrchestratorToolAllocation
    phase: Literal["policy", "planning", "selecting_tools", "executing_steps", "synthesizing", "reflection"]
    phase_label: str
    progress: ProgressState
    companion_policy: CompanionPolicy | dict[str, Any] = Field(default_factory=dict)
    chat_state: CompanionChatState | dict[str, Any] = Field(default_factory=dict)
    rapport_profile: CompanionRapportProfile | dict[str, Any] = Field(default_factory=dict)
    llm_trace: dict[str, Any] = Field(default_factory=dict)


class PolicyTurnPayload(RunningTurnPayloadBase):
    phase: Literal["policy"]
    phase_label: Literal["Planning companion policy"]


class PlanningTurnPayload(RunningTurnPayloadBase):
    phase: Literal["planning"]
    phase_label: Literal["Planning tool usage"]


class SelectingToolsTurnPayload(RunningTurnPayloadBase):
    phase: Literal["selecting_tools"]
    phase_label: Literal["Selecting tools"]
    routing: RoutingDecision
    plan: ExecutionPlan
    selected_tools: SelectedTools


class ExecutingCurrentStep(BaseModel):
    step_id: str
    tool_job: Literal["elder", "librarian"]
    goal: str


class ExecutingStepsTurnPayload(RunningTurnPayloadBase):
    phase: Literal["executing_steps"]
    phase_label: Literal["Executing tool plan"]
    routing: RoutingDecision
    plan: ExecutionPlan
    selected_tools: SelectedTools
    step_progress: StepProgressState
    current_step: ExecutingCurrentStep | None = None
    execution: ExecutionState


class SynthesizingTurnPayload(RunningTurnPayloadBase):
    phase: Literal["synthesizing"]
    phase_label: Literal["Synthesizing answer"]
    routing: RoutingDecision
    plan: ExecutionPlan
    execution: ExecutionState
    selected_tools: SelectedTools
    step_progress: StepProgressState
    agent_responses: list[AgentResponse] = Field(default_factory=list)


class ReflectionTurnPayload(RunningTurnPayloadBase):
    phase: Literal["reflection"]
    phase_label: Literal["Evaluating response quality"]
    plan: ExecutionPlan
    execution: ExecutionState
    selected_tools: SelectedTools
    routing: RoutingDecision | None = None
    final: dict[str, Any] = Field(default_factory=dict)


class QueuedTurnPayload(BaseModel):
    status: Literal["queued"]
    query: str
    session_id: str
    ontology_id: int
    companion_id: str
    allocated_tools: OrchestratorToolAllocation
    conversation_context: dict[str, Any] = Field(default_factory=dict)


class DoneFinalPayload(BaseModel):
    text: str
    linked_text: str
    references: dict[str, Any] = Field(default_factory=dict)


class ToolFailure(BaseModel):
    agent_id: str | None = None
    agent_name: str | None = None
    agent_job: str | None = None
    error: str | None = None


class DoneTurnPayload(BaseModel):
    status: Literal["done"]
    session_id: str
    query: str
    routing: RoutingDecision
    selected_tools: SelectedTools
    plan: ExecutionPlan
    execution: ExecutionState
    conversation_context: dict[str, Any] = Field(default_factory=dict)
    companion_policy: CompanionPolicy | dict[str, Any] = Field(default_factory=dict)
    turn_reflection: TurnReflection | dict[str, Any] = Field(default_factory=dict)
    chat_state: CompanionChatState | dict[str, Any] = Field(default_factory=dict)
    rapport_profile: CompanionRapportProfile | dict[str, Any] = Field(default_factory=dict)
    rapport_patch_applied: list[RapportPatchApplied | dict[str, Any]] = Field(default_factory=list)
    llm_trace: dict[str, Any] = Field(default_factory=dict)
    agent_responses: list[AgentResponse] = Field(default_factory=list)
    final: DoneFinalPayload
    tool_failures: list[ToolFailure] = Field(default_factory=list)


class FailedTurnPayload(BaseModel):
    status: Literal["failed"]
    query: str
    session_id: str
    ontology_id: int
    companion_id: str
    allocated_tools: OrchestratorToolAllocation
    phase: str | None = None
    phase_label: str | None = None
    progress: ProgressState | None = None
    routing: RoutingDecision | None = None
    plan: ExecutionPlan | None = None
    execution: ExecutionState | None = None
    selected_tools: SelectedTools | None = None
    step_progress: StepProgressState | None = None
    agent_responses: list[AgentResponse] = Field(default_factory=list)
    llm_trace: dict[str, Any] = Field(default_factory=dict)
    error: str


CompanionTurnPayload = (
    QueuedTurnPayload
    | PolicyTurnPayload
    | PlanningTurnPayload
    | SelectingToolsTurnPayload
    | ExecutingStepsTurnPayload
    | SynthesizingTurnPayload
    | ReflectionTurnPayload
    | DoneTurnPayload
    | FailedTurnPayload
)


class CompanionOrchestratorTurnResultResponse(BaseModel):
    job_id: int
    status: Literal["queued", "running", "done", "failed"]
    payload: CompanionTurnPayload


class ServiceStatusResponse(BaseModel):
    service: str
    status: str
    database_path: str
    shreckllm_base_url: str
    shrecknet_api_base_url: str
    active_jobs: int
