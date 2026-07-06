# SynthesisTool

## Purpose

Produces the assistant response from grounded execution output and lifecycle context.

## Inputs

1. User query
2. Execution plan and completed step results
3. Companion core personality fields
4. Rapport profile
5. Chat state
6. Lifecycle policy

## Rules

1. Use only provided execution evidence.
2. Preserve Librarian book references.
3. Call out partial or contradictory evidence explicitly.
4. Keep answer concise and grounded.

## Output

1. Final text
2. Linked references metadata assembled downstream

## Notes

Synthesis does not directly write persistent state.
