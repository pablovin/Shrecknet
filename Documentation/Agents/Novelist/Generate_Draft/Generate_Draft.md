# Novelist V4 Generate Draft Pipeline

`NovelistOrchestrator` owns the single active Novelist pipeline. The task persists
stage progress and final artifacts through the existing background-job mechanism.

1. `interpretation` calls the analysis role with the complete strict story-plan
   JSON Schema and a fixed 3,000-token output budget. A malformed, incomplete,
   oversized, or truncated result gets one retry at the same budget. The schema
   bounds each summary to 1,000 characters, with no maximum beat count and
   [additional field limits](../Novelist.md#structured-analysis-contract).
2. Oversized sources are interpreted in bounded chunks and reconciled by the same
   analysis role with the same schema. This branch exists only for input sizing.
3. `continuity` loads optional previous-session text and compatible V3 or V4
   previous-run context. Current source and its plan remain authoritative.
4. `block_planning` groups ordered adjacent beats into 800–1,200-word sections and
   carries their coarse source references forward.
5. `writing` runs sequentially. The writer receives no JSON response schema; it
   returns plain prose using relevant original source and the prior section tail.
6. `quality_gate` checks completion and obvious structural failures locally. Only
   an unusable section gets one focused retry.
7. `merging` HTML-escapes model output, wraps paragraphs, inserts the title, and
   saves `draft_text`.

V4 performs no normal-path LLM fidelity or correction call. A failed section or
analysis contract fails the run rather than persisting partial public output.
The same analysis budgets apply to chunk interpretation and reconciliation.
Warnings include the usage tag, attempt, output budget, and validation error.

Deploy the updated Celery workers to apply these limits, then submit a new run
for any failed generation; failed runs are not automatically resumed. Per-field
bounds do not guarantee that a complete plan fits within 3,000 tokens. Both
attempts retain strict validation even when a provider ignores sizing constraints;
no response is sliced to fit. Rolling back
restores the previous limits without rewriting stored runs. Historical plans
remain readable and usable as lower-authority continuity; public request/response
shapes do not change.

No database migration or backfill is required. Deployments should drain queued V3
jobs before updating workers because the Celery task name is unchanged and queued
payloads execute the code installed on the worker.
