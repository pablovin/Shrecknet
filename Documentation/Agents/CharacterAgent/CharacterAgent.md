# CharacterAgent

A `CharacterAgent` is a graph-backed simulation identity embodied by one
`EntityInstance`. It combines a background story, eight behavioural axes,
`trait_adherence`, active aspects, and active goals. It is distinct from the
SQL-backed Elder, Librarian, Architect, and Novelist agent configuration.

`POST /character-agents/{character_agent_id}/query` loads the complete active
identity in one Neo4j operation, then performs exactly three normal LLM calls:
task framing, character deliberation, and grounding verification/rendering.
Calls two and three receive only evidence selected during framing.

The caller's `system_instruction` controls the task, tone, constraints, and
output shape below the immutable service rules. It cannot replace the identity,
request internal reasoning, override security, or authorize external actions.

Trait names must use the fixed axes. Aspect and goal IDs must come from the
snapshot. Missing information remains uncertainty, unsupported claims are
rejected, and this job never adds a fourth JSON-repair LLM call.

The stages use `model_character_agent_framing`,
`model_character_agent_deliberation`, and
`model_character_agent_verification`, exposed through the existing config API.

- [Query contract](Query/Query.md)
- [Endpoints](CharacterAgent%20-%20Endpoints.md)
