# ShreckCompanion Lifecycle Docs

This folder documents the companion lifecycle tooling introduced in v1.

Documents:

- `orchestrator-pipeline.md`: deterministic pipeline, phase mapping, and efficiency rules.
- `tool-lifecycle-policy.md`: LifecyclePolicyTool contract and behavior.
- `tool-knowledge-planner.md`: KnowledgePlannerTool scope and strict constraints.
- `tool-synthesis.md`: SynthesisTool behavior and grounding rules.
- `tool-reflection-evaluator.md`: ReflectionEvaluatorTool outputs and guardrails.
- `tool-state-updates.md`: bounded chat-state/rapport patch application and persistence.

Design intent:

- Keep lifecycle order deterministic.
- Keep Elder/Librarian planning narrow and grounded.
- Improve speed through safe concurrency where dependencies allow.
- Keep updates stable via bounded, server-side validation.
