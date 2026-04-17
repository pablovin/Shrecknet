# Architect Agent Overview

The Architect Agent is responsible for analyzing narrative content and proposing structured knowledge representations, including entities, scenes, milestones, and relationships. It operates as a multi-stage pipeline, leveraging LLMs and knowledge graphs to extract, deduplicate, and relate information from user-provided story text.

## Core Responsibilities
- **Entity Extraction:** Identifies and proposes new or updated entities from narrative chunks.
- **Scene & Milestone Proposal:** Segments narrative into scenes and atomic milestones, enforcing strict temporal and structural constraints.
- **Relationship Resolution:** Resolves mentions and proposes `RELATES_TO` links between scenes/milestones and known entities.
- **Provenance Tracking:** All proposals include `DERIVED_FROM` provenance anchors.

## Pipeline Stages
1. **Entity Proposal:**
   - Extracts candidate entities from narrative text.
   - Deduplicates and reconciles with existing entities.
2. **Scene/Milestone Proposal:**
   - Segments narrative into scenes.
   - Proposes milestones (atomic, present-tense, no time jumps).
   - Enforces begin/end milestone constraints.
3. **Relationship Proposal:**
   - Resolves mentions in scenes/milestones against known and proposed entities.
   - Proposes `RELATES_TO` links (unambiguous only).

## Technical Details
- **Parallelization:** Entity and scene/milestone proposals run in parallel.
- **Strict Constraints:** Scene/milestone proposals must follow atomicity, tense, and boundary rules.
- **Output:** Returns all proposals (entities, scenes, milestones, relationships) in a unified response.
- **Provenance:** Each proposal includes a `DERIVED_FROM` field referencing the source.

## Example Output Structure
```json
{
  "proposals": [
    { "proposal_type": "new_instance", ... },
    { "proposal_type": "propose_scene", ... },
    { "proposal_type": "propose_milestone", ... },
    { "proposal_type": "propose_relates_to", ... }
  ],
  "scene_count": 2,
  "milestone_count": 5,
  "relates_to_count": 3,
  ...
}
```

## References
- See [Architect - Endpoints.md](Architect%20-%20Endpoints.md) for API usage and examples.
- See `shrecknet/app/jobs/architect/architect_v2.py` for orchestrator logic.
- See `shrecknet/app/jobs/architect/prompts.py` for LLM prompt templates.
