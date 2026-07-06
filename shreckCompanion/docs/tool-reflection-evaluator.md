# ReflectionEvaluatorTool

## Purpose

Evaluates answer quality after synthesis and proposes bounded state patches.

## Questions It Answers

1. Did the answer satisfy user intent?
2. Should one repair pass happen?
3. Should one proactive nudge happen?
4. Which chat-state/rapport patches are proposed?

## Output Contract

- `answered_user`
- `confidence`
- `user_state_estimate`
- `response_quality`
- `proactivity`
- `chat_state_patch`
- `rapport_patch`

## Guardrails

1. Reflection proposes patches; backend validates and applies.
2. No direct writes to core personality.
3. Repair is capped at one pass.
4. Proactive nudge is capped at one short message.

## Latency Strategy

Reflection should remain lightweight and bounded to avoid dominating turn latency.
