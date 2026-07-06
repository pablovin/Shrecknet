export type CompanionTurnStatus = "queued" | "running" | "done" | "failed";

export type CompanionTurnPhase =
  | "policy"
  | "planning"
  | "selecting_tools"
  | "executing_steps"
  | "synthesizing"
  | "reflection";

export type ProgressState = {
  current: number;
  total: number;
};

export type ToolProgressState = {
  total: number;
  completed: number;
  running: number;
};

export type StepProgressState = ToolProgressState & {
  current?: number;
};

export type RoutingDecision = {
  use_elder: boolean;
  use_librarian: boolean;
  reason: string;
};

export type SelectedTools = {
  elder: string[];
  librarian: string[];
};

export type AllocatedToolAgent = {
  id: string;
  name: string;
  job: string;
  ontology_ids: number[];
};

export type AllocatedTools = {
  elder: AllocatedToolAgent[];
  librarian: AllocatedToolAgent[];
};

export type CompanionChatSession = {
  session_id: string;
  companion_id: string;
  ontology_id: number;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
};

export type CompanionDefaultStyle = {
  verbosity: number;
  humor: number;
  directness: number;
  initiative: number;
};

export type PersonalCompanion = {
  id: string;
  user_id: number;
  name: string;
  avatar_url?: string | null;
  writing_style: string;
  core_traits: string[];
  archetype: string;
  voice: string;
  boundaries: string[];
  default_style: CompanionDefaultStyle;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type PersonalCompanionUpdate = {
  name?: string;
  avatar_url?: string | null;
  writing_style?: string;
  core_traits?: string[];
  archetype?: string;
  voice?: string;
  boundaries?: string[];
  default_style?: CompanionDefaultStyle;
  active?: boolean;
};

export type CompanionUserRapport = {
  user_id: number;
  companion_id: string;
  adaptive_traits: Record<string, number>;
  observed_preferences: string[];
  negative_signals: string[];
  recent_user_state: Record<string, unknown>;
  updated_at: string;
};

export type CompanionChatSessionCount = {
  ontology_id: number;
  count: number;
  limit: number;
};

export type AgentResponse = {
  ok: boolean;
  agent_id: string;
  agent_name: string;
  agent_job: string;
  answer: string;
  sources: unknown[];
  error?: string;
};

export type FinalInlineLinkOccurrence = {
  start: number;
  end: number;
  text: string;
};

export type FinalInlineLink = {
  node_id: string;
  node_name: string;
  node_type: string;
  agent_id?: string;
  scene_id?: string;
  source_entity_instance_id?: string;
  occurrences: FinalInlineLinkOccurrence[];
};

export type FinalTimelineSource = {
  node_id: string;
  node_name: string;
  node_type: string;
  scene_id?: string;
  source_entity_instance_id?: string;
  source_entity?: {
    node_id?: string;
    node_name?: string;
    node_type: "general";
  } | null;
  agent_id?: string;
  agent_name?: string;
  evidence_chunks: unknown[];
};

export type RunningTurnPayloadBase = {
  status: "running";
  query: string;
  session_id: string;
  ontology_id: number;
  companion_id: string;
  allocated_tools: AllocatedTools;
  phase: CompanionTurnPhase;
  phase_label: string;
  progress: ProgressState;
  companion_policy?: CompanionPolicy;
  chat_state?: CompanionChatState;
  rapport_profile?: CompanionRapportProfile;
  llm_trace?: Record<string, unknown>;
};

export type CompanionPolicy = {
  chat_goal: string;
  turn_intention: string;
  conversation_mode: string;
  user_need: string;
  needs_knowledge_tools: boolean;
  suggested_response_style: {
    directness: number;
    technical_depth: number;
    playfulness: number;
    initiative: number;
  };
  open_threads: string[];
  next_best_actions: string[];
};

export type CompanionChatState = {
  chat_goal: string;
  conversation_mode: string;
  current_intention: string;
  open_threads: string[];
  next_best_actions: string[];
  recent_user_state?: Record<string, unknown>;
};

export type CompanionRapportProfile = {
  adaptive_traits: Record<string, number>;
  observed_preferences: string[];
  negative_signals: string[];
  recent_user_state?: Record<string, unknown>;
};

export type CompanionTurnReflection = {
  answered_user: boolean;
  confidence: number;
  user_state_estimate: Record<string, string>;
  response_quality: Record<string, boolean>;
  proactivity: {
    should_be_proactive: boolean;
    proactivity_type: string;
    proactive_message: string;
  };
  chat_state_patch: {
    chat_goal?: string;
    current_intention?: string;
    open_threads_add?: string[];
    open_threads_resolved?: string[];
    next_best_actions?: string[];
  };
  rapport_patch: Array<{
    trait: string;
    delta: number;
    confidence: number;
    reason: string;
  }>;
};

export type PlanningStep = {
  step_id: string;
  tool_job: "elder" | "librarian";
  goal: string;
  query: string;
  depends_on: string[];
  use_prior_context: boolean;
  success_requirements: string[];
  on_failure: "stop";
};

export type ExecutionPlan = {
  strategy: "parallel" | "sequential";
  reason: string;
  steps: PlanningStep[];
};

export type ExecutionState = {
  completed_steps: Array<{
    step_id: string;
    tool_job: "elder" | "librarian";
    goal: string;
    query_used: string;
    agent_id: string;
    agent_name: string;
    ok: boolean;
    answer: string;
  }>;
  stopped_reason?: string | null;
};

export type PlanningTurnPayload = RunningTurnPayloadBase & {
  phase: "planning";
  phase_label: "Planning tool usage";
};

export type PolicyTurnPayload = RunningTurnPayloadBase & {
  phase: "policy";
  phase_label: "Planning companion policy";
};

export type SelectingToolsTurnPayload = RunningTurnPayloadBase & {
  phase: "selecting_tools";
  phase_label: "Selecting tools";
  routing: RoutingDecision;
  plan: ExecutionPlan;
  selected_tools: SelectedTools;
};

export type ExecutingStepsTurnPayload = RunningTurnPayloadBase & {
  phase: "executing_steps";
  phase_label: "Executing tool plan";
  routing: RoutingDecision;
  plan: ExecutionPlan;
  selected_tools: SelectedTools;
  step_progress: StepProgressState;
  current_step?: {
    step_id: string;
    tool_job: "elder" | "librarian";
    goal: string;
  } | null;
  execution: ExecutionState;
};

export type SynthesizingTurnPayload = RunningTurnPayloadBase & {
  phase: "synthesizing";
  phase_label: "Synthesizing answer";
  routing: RoutingDecision;
  plan: ExecutionPlan;
  execution: ExecutionState;
  selected_tools: SelectedTools;
  step_progress: StepProgressState;
  agent_responses: AgentResponse[];
};

export type ReflectionTurnPayload = RunningTurnPayloadBase & {
  phase: "reflection";
  phase_label: "Evaluating response quality";
  routing?: RoutingDecision;
  plan: ExecutionPlan;
  execution: ExecutionState;
  selected_tools: SelectedTools;
  final?: {
    text: string;
  };
};

export type RunningTurnPayload =
  | PolicyTurnPayload
  | PlanningTurnPayload
  | SelectingToolsTurnPayload
  | ExecutingStepsTurnPayload
  | SynthesizingTurnPayload
  | ReflectionTurnPayload;

export type QueuedTurnPayload = {
  status: "queued";
  query: string;
  session_id: string;
  ontology_id: number;
  companion_id: string;
  allocated_tools: AllocatedTools;
};

export type DoneTurnPayload = {
  status: "done";
  session_id: string;
  query: string;
  routing: RoutingDecision;
  plan: ExecutionPlan;
  execution: ExecutionState;
  companion_policy?: CompanionPolicy;
  turn_reflection?: CompanionTurnReflection;
  chat_state?: CompanionChatState;
  rapport_profile?: CompanionRapportProfile;
  llm_trace?: Record<string, unknown>;
  rapport_patch_applied?: Array<{
    trait: string;
    before: number;
    after: number;
    delta_requested: number;
    delta_applied: number;
    confidence: number;
    reason: string;
  }>;
  selected_tools: SelectedTools;
  agent_responses: AgentResponse[];
  final: {
    text: string;
    linked_text: string;
    references: {
      inline_links: FinalInlineLink[];
      timeline_sources: FinalTimelineSource[];
    };
  };
  tool_failures: Array<{
    agent_id: string;
    agent_name: string;
    agent_job: string;
    error: string;
  }>;
};

export type FailedTurnPayload = {
  status: "failed";
  query: string;
  session_id: string;
  ontology_id: number;
  companion_id: string;
  allocated_tools: AllocatedTools;
  phase?: CompanionTurnPhase;
  phase_label?: string;
  progress?: ProgressState;
  routing?: RoutingDecision;
  plan?: ExecutionPlan;
  execution?: ExecutionState;
  selected_tools?: SelectedTools;
  step_progress?: StepProgressState;
  agent_responses?: AgentResponse[];
  llm_trace?: Record<string, unknown>;
  error: string;
};

export type CompanionTurnPayload =
  | QueuedTurnPayload
  | RunningTurnPayload
  | DoneTurnPayload
  | FailedTurnPayload;

export type CompanionTurnResultResponse = {
  job_id: number;
  status: CompanionTurnStatus;
  payload: CompanionTurnPayload;
};

export type CompanionWorldBootstrapResponse = {
  companion_id: string;
  ontology_id: number;
  allocated_tools: AllocatedTools;
  existing_chat_count: number;
  chat_limit: number;
};
