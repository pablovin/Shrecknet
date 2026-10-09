# Embodiment structured output reliability plan

**Status:** implemented on 2026-10-09; live-provider quality/cost evaluation and
worker rollout remain deployment checks. The design below records the review and
implementation sequence, including event-reference and lifecycle correction
changes present at the start of the review. The maintained runtime contract is in
[CharacterAgent](CharacterAgent.md) and its
[endpoint reference](CharacterAgent%20-%20Endpoints.md).

## Objective and scope

Make CharacterAgent embodiment responses simpler to generate, validate every
accepted result before applying it, and bound recovery cost. Preserve source
chronology, historical transitions, deterministic trait aggregation, reviewed
drafts, and public API/SDK contracts.

The scope is embodiment: identity description, incorporation, psychological
enrichment, trait interpretation, and source-level consolidation. Query rendering
and other agents retain their existing repair behavior. No new framework,
dependency, graph model, data backfill, or additional reasoning stage is
needed. Existing retry configuration receives automatic key migration.

## Assessment of the supplied analysis at planning time

| Finding | Current repository evidence | Decision |
| --- | --- | --- |
| Native structured output and Pydantic are useful foundations | `EmbodyAgent._chat_structured()` requests `strict_json_schema()`; `_parse()` validates stage models | Retain both; provider enforcement does not replace backend validation |
| Recovery paths overlap | `_call()` can perform JSON repair, schema correction, and semantic correction independently | Replace these embodiment paths with one validation/replacement loop |
| Scene references should be backend-owned | Incorporation, enrichment, and trait interpretation already use position-bound collections | Retain this approach and validate exact cardinality inside the loop |
| Consolidation events need local references | Working tree already supplies `event-001` IDs, checks existence and event kind, and converts them to persisted numeric positions | Keep these local IDs; replacing them with integers offers little additional benefit |
| Consolidation has no semantic validator | No longer fully true: working tree passes `validate_event_references`, also checking status presence and update/reinforce status misuse | Expand this into complete consolidation validation |
| Model-generated stable IDs are unnecessary | `convert()` requires `candidate_id == _stable_profile_id(kind, label)` | Generate IDs in the backend |
| One operation schema is too permissive | `_ConsolidationOperation` mixes aspect/goal fields, five statuses, and many conditional nullable fields | Introduce separate typed operations for each entity and operation |
| Optional candidates may be dropped | `_drop_ungrounded_output_items()` is restricted to `EmbodimentObservationsOutput`; active compact stage outputs use other schemas | Verify callers before changing/removing it; do not add permissive dropping to active stages |

Additional issues found during tracing:

- `parse_json_deterministically()` scans for any complete nested JSON value after
  whole-document parsing fails. A broken outer document can therefore be mistaken
  for a complete inner value. Embodiment needs a stricter acceptance policy.
- Provider `finish_reason == "length"` and empty-body checks cover the initial
  response, but are not uniformly applied to correction and repair responses.
- Exact scene-count checks in `_bind_llm_perspectives()`,
  `_bind_llm_enrichments()`, and `_interpret_traits_batch()`, and duplicate-trait
  checks, can fail after `_call()` has accepted an output. Native schema list
  bounds alone are insufficient when a provider ignores them or falls back.
- Consolidation target/type checks, candidate-ID checks, conversion into
  `AspectUpdateData`/`GoalUpdateData`, and final focus validation still occur
  outside correction. Conversion can raise a raw Pydantic error.
- Consolidation shares `candidate_ids` across aspect and goal conversion, and
  checks new candidates against one combined set. Validation should use separate
  typed namespaces and reject additions colliding with existing IDs.
- `_stable_profile_id()` normalizes labels to ASCII slugs. Different punctuation
  or non-ASCII labels can collide. Removing model-owned IDs does not itself fix
  collisions; they must be detected without changing existing identifiers.
- JSON repair calls bypass `UsageTracker.chat()`, so repair usage is not recorded
  consistently with generation/correction usage.
- `structured_output_is_unsupported()` uses broad text markers. A generic
  unsupported error can trigger fallback. Tightening this shared helper affects
  other agents and belongs in a separately tested integration change.

The backend can prove reference membership, entity kind, provenance, structural
rules, and explicitly defined transitions. It cannot deterministically prove that
arbitrary natural-language evidence entails a goal's completion. Keep that
interpretive responsibility explicit in prompts and quality evaluation.

## Design decisions

### Keep public contracts; simplify only the LLM contract

Keep `AspectUpdateData`, `GoalUpdateData`, `EmbodyAgentResult`, timeline projections,
draft payloads, persisted numeric `event_references`, and graph assignments.
Translate the new model-facing contract into these existing forms. Existing
reviewable drafts must remain readable and applicable.

Use ordered `aspect_operations` and `goal_operations` with separate typed
`add`, `update`, `status`, and `reinforce` variants. Prefer a discriminated union
inside each list, subject to actual provider schema compatibility checks. Splitting
everything into independent operation lists would require another sequencing
contract and could lose valid add/update/status chronology.

Contract requirements:

- `add` contains content, justification, and nonempty event references, with no
  `candidate_id` or existing target. New items start active; later explicit status
  operations preserve same-source resolution history.
- `update` contains the permitted content changes, a target, justification, and
  event references. `reinforce` has its own explicit schema preserving the
  currently supported behavior. Neither accepts lifecycle status.
- Aspect `status` permits only `active` or `inactive`; goal `status` permits only
  `active`, `completed`, `abandoned`, or `superseded`. Status operations contain
  no content-update fields.
- Do not impose new lifecycle prohibitions implicitly. Document the existing
  reactivation contract and transition rules from their authoritative lifecycle
  implementation; do not create a validator-owned transition matrix.
- Existing targets use one-based indexes into the supplied ordered aspect or goal
  table, with the explicit scope `{"scope":"existing","index":1}`. Newly
  generated aspects and goals are self-contained in `new_aspects` and `new_goals`;
  their ordered changes are nested within each item and require no generated index.
  Indexes are strict positive integers, excluding booleans and coerced strings.
- Focus lists reference only existing items and are checked against the final
  reducer-produced state. Preserve model selection order and the cap of ten per
  kind. Never truncate invalid focus lists. A new item's `in_focus` is its final
  focus preference, subject to its final lifecycle status.
- Events retain supplied source-local IDs such as `event-001`. The backend owns
  their ordered table, canonical scene IDs, and persisted numeric positions.
- Every reference is resolved against the immutable request snapshot. No
  clamping, replacement, sorting of operations, or inference of unknown targets.

A new goal may contain an ordered `changes` array with updates and lifecycle
transitions. The backend materializes the new item and its nested changes in
sequence, preserving the stable ID and every history entry. Renaming an existing
item preserves its ID. An add colliding with an existing item or another addition
is rejected with a precise error; the model may regenerate an update/reinforce
where justified.

### Validate and materialize before acceptance

Keep consolidation validation a small boundary adapter from typed LLM output and
request context to existing backend operations and final focus IDs. Its scope is
limited to checking LLM-owned fields, resolving backend-owned references and IDs,
validating through existing lifecycle rules, and producing existing update
objects. It must:

1. Validate local event existence, kind, nonempty citations, and canonical
   source-scene provenance.
2. Resolve existing targets for aspects and goals; validate each self-contained
   addition and its nested changes, and reject wrong-kind events or ID collisions.
3. Check operation-specific LLM content, including nonblank first-person aspect
   statements and meaningful content updates.
4. Construct and validate all `AspectUpdateData` and `GoalUpdateData` values,
   including names/titles while materializing each nested new-item change.
5. Pass those objects in their supplied order to the existing lifecycle rules and
   profile reducers on copies of the starting state. Preserve valid sequences
   such as update then status, nested add then completion, and explicit reactivation.
   Lifecycle acceptance and state changes must come from the same implementation
   used when applying/replaying the operations.
6. Check focus reference uniqueness, kind, bounds, and existence at the boundary;
   delegate active-state eligibility and focus behavior to the authoritative
   profile lifecycle implementation.

Return the prepared operations only after all checks pass. Avoid a validator plus
a second independent converter implementing the same rules. **Reusing the existing
profile reducers and lifecycle rules is mandatory**, including `_apply_aspect_ops`
and `_apply_goal_ops` in `profile.py`. The adapter must not independently implement
active/completed/abandoned state, transition policy, or focus eligibility. If a
required lifecycle check currently exists only in `_consolidate_profile()` or is
missing from the shared lifecycle owner, move or add it there and have validation,
application, and replay use that single rule. Do not retain a duplicate in the
adapter or introduce a generic validation/state-machine framework. Deterministic
programming errors should surface as internal failures rather than being sent to
the model as correction requests.

### One bounded response lifecycle

Refactor the existing `_call()` implementation into the single stage lifecycle;
adding a parallel `generate_validated()` wrapper is unnecessary. A focused
job-local validation module is appropriate if extraction reduces this file's
responsibilities; business rules stay out of integrations, routers, and tasks.

Every generation and replacement goes through:

1. Existing native structured-output request and explicit compatibility fallback.
2. Empty/truncated response checks using that attempt's metadata.
3. Conservative parsing, then Pydantic validation.
4. Stage-specific cardinality, uniqueness, reference, and domain validation;
   prepare backend-bound records as part of acceptance where needed.
5. Return the fully validated result, or regenerate from original context with
   the rejected output and precise validation errors.

Use one shared replacement budget across JSON, schema, reference, and lifecycle
errors: default one replacement after the initial generation. Remove the separate
embodiment LLM JSON-repair call. Malformed commas/brackets are not assumed to be
unambiguous; regenerate rather than reconstructing evidence.

Rename the canonical setting to
`character_agent_embodiment_validation_retries`: the total invalid-output
replacement budget, retaining range 0–3 and default 1. With value zero, invalid
output fails immediately. Identity generation follows the same budget.

Keep `character_agent_embodiment_semantic_correction_attempts` as a temporary,
deprecated input alias for a documented compatibility release. Both names resolve
to one canonical value, never separate budgets. When both are supplied together,
the new name takes precedence. Migrate an existing persisted legacy value before
seeding the new default when no canonical value exists, so custom retry limits
are preserved. Normalize legacy config updates to the canonical key; config reads
and new writes use the new name. Update seed files, config API schemas, config
documentation, job/service call sites, and the constructor argument together;
retain a temporary constructor alias where callers require compatibility. Alias
removal belongs in a later, explicitly documented compatibility change. Document
that the migrated value now covers all invalid outputs rather than semantic
errors alone.
Retain existing public diagnostic fields; if the legacy semantic correction
counter remains, continue counting semantic replacements specifically rather than
silently repurposing it for all retries.

Provider unavailable/transport errors are categorized independently and do not
trigger content correction. Preserve existing job ownership and terminal-state
behavior. Terminal content failures retain their actual JSON/schema/reference
category and are not advertised as still automatically retryable.

Truncation recovery remains stage-specific: incorporation has a shorter recovery
prompt; psychological batches may split until single-scene requests. These are
new generation units, not JSON repair. Each unit has its own shared replacement
budget and metadata checks. Document and test the finite call bound, including
native-format fallback. For an enrichment batch of `n` scenes, a complete binary
split tree has at most `2n - 1` units. No truncated partial output is accepted.

Allow whitespace, an enclosing JSON code fence, and the currently documented
unambiguous collection wrappers. Reject incomplete outer documents, multiple
candidate documents, trailing explanatory text, duplicate object keys, and
nonstandard numeric values. Implement this embodiment acceptance policy without
silently changing shared parsing behavior for other agents. Schema strictness
should focus on identifiers/references and existing field constraints, avoiding
an unrelated rewrite of every scalar coercion policy.

Active stage outputs use all-or-nothing acceptance. Optional lists may be empty;
malformed members are not silently dropped. Audit legacy observation helpers and
the `observations_unavailable` branch before removal. Any retained legacy partial
acceptance must have an explicit, separate contract and verified callers.

## Delivery sequence and acceptance gates

Each phase includes focused regression tests and the applicable canonical docs;
documentation is not postponed to a final cleanup change.

| Phase | Priority and dependency | Changes and owners | Acceptance gate |
| --- | --- | --- | --- |
| 1. Establish behavior baseline | P0; first | Preserve current user changes; characterize `_call`, parsing, Stage 4 conversion, and reducers in existing embodiment/consolidation tests | Failing regression cases reproduce late target/focus/count failures, stable-ID collisions, correction truncation, and chained recovery cost |
| 2. Complete consolidation acceptance | P0; after 1 | `embody_agent.py`, authoritative lifecycle rules/reducers in `profile.py`, and optionally a small job-local adapter; move acceptance into the correction boundary using the current wire shape first | Unknown targets, cross-kind candidates, invalid entity statuses, duplicate/colliding IDs, invalid focus, and conversion errors receive bounded correction; failure changes no source state; validation/application/replay share lifecycle rules with no duplicate state logic |
| 3. Unify generation recovery | P0; after 2 | `_call`, stage validators, usage/debug artifacts, config store/API/seed/docs and caller updates; rename retry setting with temporary alias; remove embodiment-only LLM repair, close late scene-count and duplicate-trait checks | All attempts get identical checks; JSON→schema→semantic failures cannot obtain separate budgets; legacy config values migrate without resetting custom limits; usage includes every returned response |
| 4. Simplify consolidation output | P1; after 3 | Typed LLM schemas, local target tables, backend ID binding, `embody_agent_prompts.py`, prompt version; adapt to unchanged backend update schemas | Model never computes canonical IDs; add→update/status and focus-on-new-item work; old stored timelines/drafts still validate |
| 5. Verify callers and release | P1; after 4 | Celery draft path, `append_created_scenes`, timeline replay, reviewed create/update persistence, diagnostics, canonical docs and SDK compatibility checks | Both entry paths use the same acceptance rules; no writes for a failed source; earlier valid append revisions retain their existing commit semantics |
| 6. Tighten provider fallback | P2; separate integration change after 3 | `integrations/llm/structured_output.py`, client/provider boundary tests and `shreckLLM` only if required | Fallback occurs for an explicit structured-format incompatibility, not unrelated timeout/auth/model errors; affected other-agent tests remain green |

Before Phase 4 lands, verify the actual generated union schema through the
configured provider adapters, including schema-unsupported fallback. If a provider
cannot handle nested discriminators, use a compatible schema representation of
the same typed contract; do not silently revert to a permissive optional-field
object. Schema emission and runtime validation must describe the same rules.

## Verification plan

Extend existing tests rather than creating a parallel test harness:

- `test_character_psychological_consolidation.py`: every operation variant,
  entity-specific enums, empty/unknown/wrong-kind event references, invalid target
  existing indexes, nested lifecycle changes, collisions, same-source goal
  completion, reactivation, ordered updates, and final focus validity.
  Verify validation and application/replay agree through the same reducers and
  lifecycle rules, with no mutation of the starting state during acceptance.
- `test_character_embodiment.py`: malformed/ambiguous JSON, conservative wrappers,
  empty/length-stopped initial and corrected responses, exact scene cardinality,
  duplicate trait candidates, and default/zero/exhausted replacement budgets.
- Test a sequence where the first response has invalid JSON and its replacement
  has a semantic error: fail after two generations at the default budget.
- Verify unavailable/timeout errors remain categorized on every attempt; test
  structured fallback separately from content regeneration and truncation splits.
- Extend existing configuration tests for the new canonical retry name, default
  and bounds, legacy-only input, both-name precedence, persisted-value migration
  before default seeding, normalized legacy updates, and temporary constructor
  alias behavior. Verify job and append paths receive the same canonical budget.
- `test_character_embodiment_debug_artifacts.py`: attempt reason, error location,
  raw/parsed response, metadata, usage tags, and unavailable/failed-attempt
  diagnostics. Use existing artifact storage and access policy.
- `test_character_embodiment_jobs.py` and `test_character_trait_persistence.py`:
  failed-source isolation, unchanged owner/admin controls, stale revision and
  changed scene-scope rejection, draft finalization, append transaction boundaries,
  replay of existing stored projections, and reviewed draft application.
- Existing SDK compatibility tests confirm unchanged public payloads. Run SDK
  tests from `python_sdk/` if any public producer or consumer changes.

Run from `shrecknet/` in this order:

```bash
python -m pytest tests/test_character_psychological_consolidation.py tests/test_character_embodiment.py
python -m pytest tests/test_character*.py
python -m pytest
```

The full suite is a practical release check; report unrelated/environment failures
separately. No live provider availability is implied by unit-test success. Before
rollout, replay representative diagnostic fixtures and run a small provider/model
matrix covering sparse events, many events, long sources, add-and-complete goals,
and non-ASCII/colliding labels. Compare first-pass validity, eventual validity,
failure categories, provider calls, completion tokens, and latency against the
same baseline corpus. Review evidence fidelity manually: valid JSON and valid
references do not prove sound psychological interpretation. Require zero accepted
invalid references/transitions and no regression in the agreed evidence-quality
review; establish cost/latency thresholds from baseline measurements rather than
inventing percentages.

## Documentation, rollout, and rollback

Update `CharacterAgent.md` and `CharacterAgent - Endpoints.md` with the accepted
pipeline, retry budgets, categories, truncation recovery, local references,
source atomicity, and unchanged reviewed-draft/public payload contract. Keep the
configuration reference and seed examples synchronized with the new retry name,
legacy alias, precedence, persisted-value migration, and deprecation window. Keep the
prompt module docstring, source headers, definition order, complete input/output
contracts, and canonical stage flow synchronized. Increment `PROMPT_VERSION` when
the wire contract changes. Update the existing psychological redesign record
only where it would otherwise contradict the maintained contract; link rather
than copying the new design. Update SDK documentation/examples only for affected
publicly visible behavior; no SDK schema change is planned.

Drain or finish active embodiment work before deploying a prompt/schema change,
and restart the relevant workers with matching code and prompts. Check any reuse
or checkpoint mechanism before allowing old intermediate LLM output into the new
contract. Already persisted public drafts/revisions retain their existing shapes
and identifiers; no backfill is planned. Roll back code, prompts, and worker
versions together, then regenerate failed in-flight work. Do not rewrite persisted
IDs or delete valid history as part of recovery.

Append processing currently commits source runs individually; this plan does not
promise request-wide rollback of earlier completed sources or identity refresh.
Verify and document those boundaries explicitly. Changes to that transaction
policy require a separate design.

Completion requires code, tests, prompts, and canonical documentation to agree;
no duplicate repair pipeline, undocumented fallback/drop behavior, or unvalidated
post-generation domain decisions may remain on the active path.

## Review baseline

During this planning review, the following focused baseline passed from
`shrecknet/`: **19 tests passed**, with three existing dependency/schema
deprecation warnings.

```bash
../.venv/bin/python -m pytest tests/test_character_psychological_consolidation.py tests/test_character_embodiment.py
```

The initial invocation could not load `conftest.py`: runtime configuration pointed
`media_root` at an unwritable `/app` directory. The successful invocation used a
fresh temporary `SHRECKNET_DATA_DIR` and `SHRECKNET_CONFIG_SEED_FILE` containing a
writable temporary `media_root`; existing configuration databases were not edited.
This establishes the current focused baseline, not validation of the proposed
implementation. The affected package suite, full suite, SDK suite, and live
provider evaluations were not run for this documentation-only change.


## Implementation and verification record

All six code phases are implemented. `consolidation.py` owns typed LLM operation
variants and local reference resolution. `profile.py` remains the sole lifecycle
and focus owner for acceptance, application, and replay. `_call()` now uses one
invalid-output retry budget and checks all attempts consistently. The deprecated
retry setting migrates before default seeding and remains an input alias. The
shared provider fallback now requires explicit format incompatibility. Usage
accounting also avoids double-counting analysis calls when one agent performs
analysis and consolidation.

Operation-literal variants emit provider-compatible `anyOf` rather than requiring
`oneOf`/discriminator support. The actual generated schema passed JSON Schema
validation and offline adapter checks for OpenAI, OpenRouter, DeepInfra and
Ollama; Anthropic preserves its explicit format incompatibility for fallback.
These adapter checks use fake transports and do not establish live-model output
quality or availability.

Automated verification from the owning package directories:

- CharacterAgent, retry configuration, and Architect structured-output regression
  command: `python -m pytest tests/test_character*.py tests/test_embodiment_validation_config.py tests/test_architect_parallel_structured_extraction.py`
  — 186 passed, with three existing deprecation warnings.
- Broader Elder/Librarian/Novelist, configuration, and provider-preflight regression
  command: `python -m pytest tests/test_elder*.py tests/test_librarian*.py tests/test_novelist*.py tests/test_shreckllm_client_provider_preflight.py tests/test_email_verification_config.py tests/test_public_registration_config.py tests/test_foundry_configuration_schema.py`
  — 66 passed; the existing
  `test_chat_polling_has_no_caller_generation_deadline` failed because the unchanged
  client supplies `60.0` rather than the test's expected `None` timeout. Other-agent
  and configuration checks were also rerun separately after the final fallback
  change — 62 passed.
- `python_sdk/`: `python -m pytest` — 27 passed.
- `shreckLLM/`: `python -m pytest tests/test_openai_client.py tests/test_ollama_client.py`
  — 14 passed.
- Backend `python -m pytest` could not collect three existing test modules:
  `test_architect_scene_milestone_relates_to.py` imports missing
  `app.jobs.architect.architect_v2`; `test_ontology_world_stats_endpoint.py` imports
  missing `OntologyInstance`; `test_scene_centric_chunking.py` imports missing
  `_normalize_scene_ranges`. These unrelated implementations were not changed.
- Python compilation and `git diff --check` passed. Tests used an isolated temporary
  config/data directory and writable media root, leaving runtime databases intact.

Initial development checks exposed and corrected a model-field type assumption,
a legacy seed normalization omission, and test-fixture setup errors. One initial
pytest invocation ran from the repository root and found no requested test files;
subsequent commands used the owning package directories above.

Live-provider corpus evaluation and measured first-pass validity, evidence quality,
cost and latency comparisons were not run. Drain/restart workers and perform those
release checks before production rollout. No running deployment was modified.
