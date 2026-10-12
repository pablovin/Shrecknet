# Novelist V5 Generate Draft Pipeline

`NovelistOrchestrator` owns generation; the Celery task owns persistence/tracking.
The [canonical contract](../Novelist.md) defines schemas, budgets, provenance,
observability, compatibility, and local-check limitations.

1. Backend indexing preserves all canonical text, exact offsets, and available PDF
   page provenance as `paragraph-v1` passages.
2. `interpretation`: Stage 1 `build_story_plan_prompt` frames the whole source into
   `CandidateExtraction`: ordered candidates, source ranges, material classification,
   confirmed/unresolved facts, and explicit/ambiguous/GM identities.
3. Oversized branch: Stage 1a `build_inventory_prompt` gathers factual evidence from
   bounded windows; Stage 1b `build_reconciliation_prompt` synthesizes every inventory
   into one candidate extraction. No local arc quotas or parallel branches run.
   Over-budget synthesis fails without dropping windows.
4. Backend source-reference validation runs inside the structured repair boundary.
   `build_analysis_retry_prompt` regenerates a complete rejected structured result
   once, for extraction, inventories, synthesis, or Director. Initial and repair
   analysis calls use strict schemas, preflight, and 6,000 output tokens.
5. Stage 2 `build_director_prompt` reasons over candidate JSON, original instructions,
   and language only. `direct_story` returns validated chronological arcs and exact
   fact references. Python derives canonical cast and ambiguous/GM restrictions
   from candidate identities before validation. No original passages, historical continuity, or prose reach it.
   Director activity uses the existing `interpretation` progress stage.
6. Backend validation checks candidate accounting, chronology, identity restrictions,
   gameplay exclusions, and fact references; it does not verify semantic fidelity.
7. `continuity` retains prior session and V3/V4/V5 context. Writer context receives
   bounded matching prior narrative strings, without historical reference objects.
8. `block_planning` allocates an independent chapter word target across Director arcs.
   Context/output-limited subdivisions retain arc identity and ordered evidence.
9. `writing`: Stage 3 `build_writer_prompt` renders units sequentially from canonical
   identities, a short global outline, current developments, locally resolved original
   passages, instructions/language, bounded prior context, and a 700-character accepted
   prose tail. It receives no complete candidate plan or other arcs' detailed facts.
10. Local formatting/completion, identity/gameplay leakage, and repetition checks run.
    Stage 3r `build_writer_retry_prompt` replaces a rejected unit once. Only accepted
    prose becomes continuity. Exhausted repair never publishes a partial chapter.
11. `quality_gate` confirms all units passed local checks; `merging` escapes title/prose
    into safe chapter HTML. Artifacts remain V5 with revision `narrative-director-v2`.

Prompts have no retained inactive variants or mandatory semantic reviewer. Debug
snapshots retain requests before dispatch, responses before validation, metrics,
repairs, and failures. Normal calls are one extraction, one Director, and one writer
per arc; fallback, subdivisions, and retries add calls explicitly.

Rebuild workers, drain queues during cutover, and submit new runs for failures.
No database migration or new model setting is needed. Historical artifacts remain
readable. Real model quality requires annotated-session evaluation separately from
automated contract tests.
