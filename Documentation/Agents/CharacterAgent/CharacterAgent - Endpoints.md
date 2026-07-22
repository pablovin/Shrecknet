# CharacterAgent endpoints

## Query

`POST /character-agents/{character_agent_id}/query` requires administrator
authentication and enabled AI agents. It performs one Neo4j snapshot operation
and exactly three normal LLM calls, has no graph write side effects, and does not
persist internal framing or deliberation.

See [CharacterAgent Query](Query/Query.md) for request and response contracts.

Existing CRUD remains under `/character-agents`, `/character-aspects`, and
`/character-goals`. `trait_adherence` is an integer from 0 through 100, defaults
to 80, and is read as 80 for older graph records where it is absent.
