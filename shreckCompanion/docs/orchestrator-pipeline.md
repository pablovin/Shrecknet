# Orchestrator Pipeline

## Deterministic Lifecycle Order

The lifecycle executes in this logical order:

1. Load state
2. LifecyclePolicyTool
3. KnowledgePlannerTool
4. Execute knowledge tools
5. SynthesisTool
6. ReflectionEvaluatorTool
7. Optional repair (max one)
8. Optional proactive nudge (max one)
9. Apply bounded state updates
10. Persist final payload

This order is fixed for behavior stability and auditability.

## Runtime Phase Mapping

While a turn is `running`, payload `phase` maps to six observable phases:

1. `policy`
2. `planning`
3. `selecting_tools`
4. `executing_steps`
5. `synthesizing`
6. `reflection`

The optional repair and proactive steps are internal post-reflection actions and are represented in lifecycle metadata inside the final payload.

## Concurrency And Efficiency

Deterministic order does not forbid all parallelism. Use dependency-aware concurrency:

1. Knowledge execution can run parallel only for steps with no `depends_on` constraints.
2. If Librarian step depends on Elder canon grounding, it must remain sequential.
3. Reflection can be optimized to reduce perceived latency, but only if persistence semantics remain safe and final payload remains consistent.
4. Non-LLM transforms should be local and deterministic to reduce token and latency costs.

## Token Efficiency Guidelines

1. Keep policy and reflection prompts compact and JSON-only.
2. Reuse summarized context instead of replaying full history.
3. Avoid extra repair pass unless evaluator explicitly flags it.
4. Use strict schemas and normalization to reduce retries.

## Guardrails

1. Knowledge planner remains Elder/Librarian-only.
2. Core personality is never auto-mutated.
3. Rapport updates are confidence-gated and bounded.
4. Proactive nudge is optional, short, and tied to chat goal.
