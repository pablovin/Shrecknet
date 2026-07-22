# CharacterAgent Query

The service accepts an open task and does not assume a scene, choices, or a
decision. It can produce dialogue, analysis, a letter, an option selection,
plain text, or caller-contracted JSON.

```json
{
  "query": "The northern clans demand that you surrender your brother. What do you do?",
  "system_instruction": "Use a short first-person declaration.",
  "context": {"location": "The royal hall"},
  "response_format": {"type": "text"},
  "generation": {"mode": "simulation", "temperature": 0.7, "max_tokens": 500}
}
```

Text responses use `{"type":"text","content":"..."}`. For structured output,
set the type to `json` and optionally supply a JSON Schema in `schema`; `content`
must validate against it.

The endpoint is admin-only in Phase 1. It returns `404` for a missing character,
`409` for an inactive character, `422` for invalid input, `502` for invalid
generation output, and `503` when agents or shreckLLM are unavailable.
