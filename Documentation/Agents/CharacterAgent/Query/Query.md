# CharacterAgent Query

The service accepts an open task and does not assume a scene, choices, or a
decision. It can produce dialogue, analysis, a letter, an option selection,
plain text, or caller-contracted JSON.

`POST /character-agents/{character_agent_id}/query` supports two execution
modes through `use_character_identity`:

- `true` is the default and runs the identity-grounded CharacterAgent
  simulation.
- `false` runs one generic LLM call without supplying CharacterAgent identity,
  traits, aspects, goals, or other graph data.

The endpoint still verifies that the requested CharacterAgent exists, is
visible to the caller, and is active in both modes. Generic mode therefore does
not turn the route into an unscoped general LLM endpoint.

## Identity-grounded request

```json
{
  "query": "The northern clans demand that you surrender your brother. What do you do?",
  "use_character_identity": true,
  "system_instruction": "Use a short first-person declaration.",
  "context": {"location": "The royal hall"},
  "response_format": {"type": "text"},
  "generation": {"temperature": 0.7}
}
```

Identity-grounded mode loads the active character identity in one Neo4j
operation and performs exactly three normal LLM calls:

1. Frame the task and select relevant traits, aspects, and goals.
2. Deliberate using only the selected CharacterAgent evidence.
3. Verify grounding and render the public response.

The service does not persist the frame, deliberation, or response and makes no
graph writes. The service does not pass an explicit output token cap to
shreckLLM for framing, deliberation, or verification.

## Generic request

```json
{
  "query": "Summarize the advantages and disadvantages of accepting the treaty.",
  "use_character_identity": false,
  "system_instruction": "Give a neutral answer in three bullet points.",
  "context": {"city_supply_days": 14},
  "response_format": {"type": "text"},
  "generation": {"temperature": 0.3}
}
```

Generic mode performs a minimal Neo4j visibility and active-status check,
followed by exactly one call using `model_character_agent_deliberation`. The LLM
receives only `query`, `context`, `system_instruction`, and `response_format`.
It does not receive the CharacterAgent ID or any CharacterAgent profile data.
The operation has no graph write side effects.

## Request and response contract

Only `query` is required. `use_character_identity` defaults to `true`;
`response_format.type` defaults to `text`; and `generation` defaults to
`{"temperature":0.7}`.

The service supplies no explicit output token budget. Requests containing the
removed `generation.mode` or `generation.max_tokens` fields are rejected with
`422`.

Text responses use `{"type":"text","content":"..."}`. For structured output,
set the type to `json` and optionally supply a JSON Schema in `schema`; `content`
must validate against it in both execution modes. Remote JSON Schema references
are rejected; local references beginning with `#` are allowed.

The endpoint requires authentication. Administrators may query any active
CharacterAgent; other authenticated users may query only active public agents.
It returns `404` for a missing character or a character that is not visible to
the caller, `409` for an inactive character, `422` for invalid input, `502` for
invalid generation output, and `503` when agents or shreckLLM are unavailable.
