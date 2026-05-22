# Agents and Types

Use `python_sdk/examples/06_agents_management/01_agents_management_basics.py`.

## Agent fields

- `name` required: display name.
- `job` required: job type returned by `/agents/jobs`.
- `active` required: must be true for runtime usage.
- `description` optional: purpose/context.
- `writing_style` optional: behavior/persona guidance.
- `avatar_url` optional.
- `ontology_ids` on create: ontologies to link.

## Common job types

- `elder`: conversational retrieval/orchestration.
- `architect`: structure and world modeling workflows.
- `librarian`: library/document intelligence workflows.
- `novelist`: drafting/narrative generation workflows.
