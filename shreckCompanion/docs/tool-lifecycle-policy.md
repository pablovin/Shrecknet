# LifecyclePolicyTool

## Purpose

Sets the turn stance before knowledge planning. It answers:

1. What is the turn type?
2. What is the chat goal now?
3. What does the user need right now?
4. What response style should we use?
5. Do we need knowledge tools this turn?

## Inputs

1. User query
2. Recent conversation context
3. Chat state
4. Rapport profile

## Output Contract

Returns strict JSON:

- `chat_goal`
- `turn_intention`
- `conversation_mode`
- `user_need`
- `needs_knowledge_tools`
- `suggested_response_style` with bounded values in `[0.0, 1.0]`
- `open_threads`
- `next_best_actions`

## Constraints

1. No tool execution from this step.
2. No canon/rules invention.
3. No rapport patch writing.
4. Policy is guidance for downstream stages.

## Failure Behavior

If policy parsing fails, system falls back to deterministic defaults.
