# Novelist V3 Generate Draft Pipeline

`NovelistOrchestrator` owns the single Novelist pipeline. The task persists each
stage and reports progress through the existing background-job mechanism.

1. `interpretation` calls the analysis role. Large source containers are split
   only for transport, interpreted per segment, and reconciled into one
   provenance-validated evidence ledger.
2. `continuity` loads optional previous-session text and a completed V3 prior
   ledger. The current ledger always outranks it.
3. `block_planning` groups chronological adjacent scenes deterministically.
4. `writing` and `quality_gate` run sequentially for each block. The writer has
   no authority to alter ledger facts.
5. `merging` adds the backend-owned `<h1>` and joins accepted blocks.
6. `fidelity` uses the analysis role to return affected block IDs. `correction`
   is exceptional, targeted, and followed by exactly one final verification.

No V1/V2 fallback path exists. Deployments should drain queued V2 jobs before
upgrading; a failed or in-flight old run must not be resumed by V3.
