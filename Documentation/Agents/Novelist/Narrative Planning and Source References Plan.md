# Novelist Narrative Director: Design and Evaluation

Status: implemented as the focused V5 revision `narrative-director-v2`. The
[runtime contract](Novelist.md) and [ordered flow](Generate_Draft/Generate_Draft.md)
are authoritative. Live generation/token/latency and prose-quality comparison
remain pending; the annotated context projection below is not a live-model result.

## Design and ownership

```text
Canonical source → paragraph-v1 index → candidate evidence extraction
  Oversized branch: bounded factual inventories → global candidate synthesis
Candidate JSON + instructions + language → Narrative Director → ordered arcs
Arcs → deterministic budgets/subdivisions → sequential prose → local checks → HTML
```

- `story_plan.py` owns candidate, identity, fact-reference, and Director contracts.
  The legacy `story_plan` projection keeps existing artifact fields compatible.
- `source_interpreter.py` owns the existing full-source/factual-window paths and
  source reference validation. Both paths preserve uncertainty and identity evidence.
- `narrative_director.py` owns one editorial analysis call with a narrow input
  boundary. It has no source passages, PDF, historical continuity, or prose input.
- `block_planner.py` owns chapter word allocation and context/output subdivisions,
  preserving each arc's narrative identity and every selected source passage.
- `prompts.py` declares complete stage contracts in execution order, including
  bounded recovery. Only the writer produces prose eligible for public output.
- `orchestrator.py` owns handoffs, sequential accepted continuity, stage callbacks,
  per-call usage, and debug artifacts. Existing persisted progress enums are reused.
- `prose_quality.py` owns conservative local format/completion, identity/gameplay,
  and repeated-prose checks plus safe HTML rendering.
- `tasks/novelist.py` preserves job persistence and historical continuity reads,
  preferring directed plans for refined V5 records.

Director facts reference candidate statements by beat ID and zero-based fact
index. This avoids rewriting uncertainty during grouping. Validation rejects
missing, duplicated, unknown, or reordered assignments; omitted facts; invented
cast mappings; resolved ambiguous identities; and gameplay-only writing assignments.
These are structural guarantees, not proof that the model interpreted the PDF
correctly. Extraction must preserve the evidence the Director needs.

## Session 9 regression evidence

`shrecknet/tests/fixtures/novelist/session9.json` contains canonical text extracted
from `Session 9.pdf`, original instructions, the measured completed V5 baseline,
and explicitly annotated corrected candidate/arc fixtures. It comes from local run
`96166c63-5f03-4949-b847-4a1f16359c4a`, completed on 2026-10-11. That trace did not
retain PDF bytes or page spans; this fixture does not establish PDF extraction or
page provenance for the original file. Separate upload tests cover those contracts.

The original instructions identify Pablo as DM, Pietro as Tamura, Laura as Lynelle,
and Enrico as Cwenhild or Asterius. The annotated fixture preserves that ambiguity,
possible healing, and unconfirmed death after falling. It retains Arthur's
recognition, the battles, Gawain's inconclusive duel, Ban's arrival, Lot's retreat,
and the land grants while excluding gameplay-only calculations. Mixed candidates
retain their fictional consequences.

Automated replay validates reference/accounting and minimal context boundaries,
bounded repair, accepted-only transitions, source/output subdivisions, historical
compatibility, and provider-usage accounting using fake provider responses. It
cannot show that a real model will discover the annotated plan or write faithful,
book-quality prose. Canonical names and required outcomes must also be reviewed
in the generated chapter during live evaluation.

## Recorded baseline and context projection

Run from `shrecknet/`:

```bash
PYTHONPATH=. ../.venv/bin/python scripts/evaluate_novelist_v5.py
```

The baseline uses `openrouter:deepseek/deepseek-v4.1-flash` for both roles. Token
counts are summed from provider-reported usage in its saved responses; bytes are
UTF-8 lengths of exact saved writer prompts.

| Metric | Recorded baseline | Annotated new-context projection |
| --- | ---: | ---: |
| Normal calls | 16 | 7 (1 extraction + 1 Director + 5 writers) |
| Writing units | 15 | 5 |
| Writer prompt bytes | 201,739 | 37,298 |
| Input tokens | 52,382 | Pending real provider execution |
| Output tokens | 11,547 | Pending real provider execution |
| Chapter length | 7741 generated words | 4,490 target words; actual prose pending |
| Total elapsed time | 83,717.75 ms | Pending real provider execution |
| Narrative quality | Baseline candidate includes GM, uncertain identity mappings, and unsupported completed healing | Annotated evidence preserves restrictions; generated prose review pending |

The context projection uses the human-authored five-arc plan, current prompt
builders, and a 700-ASCII-character transition tail after the first unit. It
reduces repeated writer input by approximately 81.5%, without increasing context or
output-token settings. It is not a measured reduction in provider tokens, latency,
or total chapter-generation cost. Changed chapter-length targets also require
reporting generated word counts during quality comparison.

To compare a real new run after ShreckLLM is available:

```bash
PYTHONPATH=. ../.venv/bin/python scripts/evaluate_novelist_v5.py \
  --result /path/to/numbered_result.json
```

The script accepts a debug result snapshot or direct orchestrator result. Use the
same PDF/instructions/language and models as the baseline. Record fallback path,
arc count, subdivisions, retries, output word count, missing usage, tokens, bytes,
and wall time. Review every annotated outcome and uncertainty, fictional identity,
excluded administrative passage, repeated opening, and transition in the prose.
No mandatory LLM semantic reviewer is introduced.

## Operations and compatibility

No database migration or new model target is required. Rebuild workers and drain
queues for cutover; queued jobs run installed worker code. Existing request and
HTML response contracts remain compatible. New artifacts add a revision and
directed plan while keeping historical records unchanged. Rollback preserves
readability of V5 artifacts; older workers can reuse the legacy plan projection.
Source and debug storage remain local and may contain sensitive session content.
See the [endpoint contract](Endpoints/Novelist%20-%20Endpoints.md) and
[SDK reference](../../../../python_sdk/docs/novelist.md).
