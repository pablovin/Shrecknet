# KnowledgePlannerTool

## Purpose

Decides Elder/Librarian execution plan only.

## Scope

Allowed tool jobs:

1. `elder`
2. `librarian`

Not allowed:

1. Rapport updates
2. Personality mutation
3. Chat-state mutation

## Key Rules

1. If rules require canon grounding, plan `elder` then `librarian` sequentially.
2. Generic rules/mechanics with no named subject route to Librarian-only.
3. Every planned step must have a non-empty query.
4. `on_failure` remains `stop`.

## Output

- `strategy`
- `reason`
- `steps[]` with dependencies and requirements

## Parallelism

Parallel strategy is allowed only for independent steps with no dependency chain.
