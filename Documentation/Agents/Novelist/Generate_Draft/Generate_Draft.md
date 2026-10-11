# Novelist V5 Generate Draft Pipeline

`NovelistOrchestrator` owns the active generation pipeline. The Celery task owns
persistence and tracking. [The canonical contract](../Novelist.md) defines schemas,
limits, configuration, source provenance, observability, and compatibility.

1. Backend indexing preserves the canonical source as ordered paragraphs/bounded
   fragments with exact offsets and optional original PDF page provenance.
2. `interpretation`: Stage 1 `build_story_plan_prompt` frames the complete session
   globally when its prompt fits the configured analysis context. It returns one
   compact dramatic plan, typically 8–10 beats, with inclusive passage ranges.
3. Oversized-input branch: Stage 1a `build_inventory_prompt` extracts chronological
   factual inventories from bounded windows. Stage 1b
   `build_reconciliation_prompt` synthesizes one global dramatic plan from all
   inventories and ordered passage IDs. If synthesis cannot fit, fail clearly;
   do not drop windows. There are no parallel planning branches or local beat quotas.
4. Stage 1r `build_analysis_retry_prompt` repairs malformed, truncated, schema-invalid,
   duplicate-ID, or invalid-range output once. The initial and repair calls use
   strict schemas and a 6,000-token budget. Recovery is subject to context checks.
5. Backend range resolution and coverage diagnostics record selections, omissions,
   and overlap. They do not independently verify semantic coverage.
6. `continuity` loads lower-authority previous-session text and V3/V4/V5 context.
7. `block_planning` creates one unit per beat, subdividing large source selections
   under the same beat identity. Targets reflect dramatic importance.
8. `writing`: Stage 2 `build_writer_prompt` runs sequentially with the overall plan,
   assigned original passages, instructions, continuity, and previous prose tail.
   Writer calls receive no JSON response schema and return plain prose.
9. Local completion checks run per unit. Stage 2r `build_writer_retry_prompt` rewrites
   an unusable unit once. `quality_gate` confirms all units were accepted.
10. `merging` escapes prose/title and renders chapter HTML. Final artifacts are
    versioned V5. No partial chapter is published on analysis or writing failure.

There are no retained inactive prompts, normal-path LLM fidelity/correction calls,
or independent coverage verifier. Prompts and responses, including failures and
repairs, are saved incrementally in the local debug area before validation.

Deploy rebuilt workers, drain old queues, and submit fresh runs for failed jobs.
No database migration is needed; historical runs remain readable. Live semantic
quality evaluation is separate from automated contract/regression tests.
