# Source Bundle Evolution Plan

Status: implemented; deterministic verification recorded below. This document
retains the pre-change findings and design reasoning. The current contract is
maintained in [CharacterAgent](CharacterAgent.md).

## Objective

Finish each chronological source bundle by applying its accepted trait evidence,
aspect operations, goal operations, focus, and subtitle changes, then regenerate
the complete `IdentityDescription`: `identity_summary`, `psychological_summary`,
and `personality_traits`. The next bundle must consume that resulting state.

“Complete update” means applying validated changes to accumulated state, preserving
unchanged items and lifecycle history. It does not mean replacing the profile with
only the latest bundle's observations or forcing numerical traits to change.

## Findings before this change

- `tasks/character_embodiment.py` already processes bundles sequentially. It applies
  `result.trait_profile`, merges evidence, and applies aspect and goal operations
  before starting the next bundle. This state is cumulative already.
- The draft worker loads or generates one initial `identity_description`, places
  it in `canonical_identity`, and refreshes it only after all bundles. Later scene
  extraction therefore combines updated structured state with an old portrait.
- `CharacterEmbodimentService.append_created_scenes()` has the same narrative gap.
  It commits each source revision separately, then saves the refreshed description
  after the entire append operation, in a separate graph transaction.
- Draft analysis checkpoints fingerprint upstream profile, evidence, aspects,
  goals, and identity description, but do not capture a completed source evolution
  including consolidation and the refreshed portrait.
- `chunk_source_scenes()` creates globally chronological contiguous source runs.
  One source can produce multiple bundles when another source interrupts it.
  Unrelated scenes without a source form separate runs.
- Draft analysis splits runs into at most five scenes. The append path currently
  passes a whole run to extraction; its local variable named `chunk` is a source
  run, not a five-scene partition. This discrepancy must be explicit in the work.

## Critical assessment

The proposed sequencing repairs stale narrative grounding. It does not guarantee
that earlier observations retain equal influence: numerical scoring, recency,
deduplication, conflicting evidence, and lifecycle changes still control evolution.
Keep those rules unchanged and test them independently of narrative synthesis.

Repeated synthesis also creates feedback risk: an earlier model interpretation
could become progressively more confident through repetition. Keep the existing
`continuity_only_not_evidence` rule, authoritative numerical scores, contributing
evidence, and bounded validated perspectives. A generated summary must never add
trait evidence or override canonical scene facts. Contradictory new behavior must
remain admissible even when the previous portrait describes another tendency.

Bundle boundaries become more consequential because each boundary changes the
next extraction context. Preserve chronological contiguous runs and existing
source-level revision boundaries. Do not regroup every scene from one source into
one block or claim invariance under different source partitioning.

## Execution contract

1. Load the persisted identity description, or synthesize the initial description
   if absent, using the existing schema and model routing.
2. Snapshot the current state for one source run. Every scene chunk in that run
   uses this same starting state; retain concurrent chunk analysis.
3. Validate and merge chunk outputs, consolidate source-level psychology events,
   aggregate traits deterministically, and apply lifecycle/focus/subtitle operations
   to a candidate next state using the existing reducers.
4. Synthesize all three identity fields from that candidate's accumulated evidence,
   traits, accepted aspects/goals, bounded perspectives seen so far, and previous
   description. Never include future bundles. Audit synthesis inputs so completed
   or inactive entries cannot be presented as current motivations merely because
   their lifecycle status was omitted from the model payload.
5. Validate the full description. Only then finalize the source's next state,
   replace `canonical_identity.identity_description`, and mark the bundle complete.
6. Start the next source with that finalized state. The last source's description
   is the final proposal/persisted description; do not add a redundant final call.

Preserve existing handling of legacy no-change results and empty append inputs;
specify and test these cases without inventing evidence or revisions.

## Implementation sequence and ownership

1. Add focused orchestration regression tests demonstrating the stale-description
   gap with at least two bundles and distinguishable outputs for all three fields.
2. Put source-state finalization in a focused shared job/service owner used by
   draft generation and append. Reuse existing reducers and synthesis rather than
   introduce a second trait or lifecycle implementation. Keep task code concerned
   with scheduling, progress, checkpoints, and job lifecycle.
3. Move synthesis inside the bundle boundary in both callers. Keep five-scene
   chunk behavior in the draft worker. Bring append extraction into the same
   partition/merge contract through a shared job helper, with one consolidation
   and one synthesis per run; do not import task helpers into services.
4. Extend draft checkpoints to retain the validated finalized source result and
   description. Fingerprint exact upstream state, source inputs, synthesis/model
   contracts, and partition policy. Cache the generated initial description when
   absent so compatible retries can reproduce the same upstream state. Reuse a
   completed source transition without applying its evidence twice. Invalidate
   downstream transitions when any upstream input changes. Bump the checkpoint
   contract version and ensure incompatible records are ignored safely.
5. In append, generate and validate the candidate before opening a write
   transaction, then persist the source timeline and refreshed description in
   the same graph transaction. A synthesis or persistence failure must leave
   that source uncommitted; previously completed sources remain committed.
6. Include synthesis in source progress, timing, usage, and debug artifacts.
   Count each new provider call once and report synthesis failures against the
   relevant source. Preserve generation-revision supersession checks.

## Verification and acceptance

- Bundle B's perspective, psychology, trait interpretation, and consolidation
  consume bundle A's finalized state and all three refreshed identity fields.
- Every chunk of A sees A's starting state; no chunk sees a partially applied A.
- Prior evidence and unrelated aspects/goals survive; explicit lifecycle and
  focus changes carry forward. Trait scoring and manual overrides retain their
  established contracts. New contradictory evidence remains admissible.
- With an existing description and N nonempty bundles, normal synthesis runs N
  times; with an absent description, N + 1. There is no duplicate final synthesis.
- An identity-generation failure prevents the next bundle from starting. Drafts
  do not become reviewable early; append commits neither half of the failed source.
- Compatible retries reuse completed transitions; changed inputs invalidate the
  affected transition and successors. No evidence or revisions are duplicated.
- Append runs larger than five scenes preserve source revision boundaries while
  enforcing the same extraction partition size as drafts.
- Verify final proposals, accepted-agent persistence, timeline replay, usage totals,
  and progress. Historical narrative snapshots are outside this change unless
  tracing consumers demonstrates they are required; do not expand public revision
  schemas merely to implement internal propagation.

Run focused embodiment orchestration, validation, consolidation, synthesis, and
append persistence tests from `shrecknet/`, then the affected CharacterAgent suite
when practical. Use deterministic model doubles for sequencing/failure assertions.
A small live comparison can assess portrait quality and feedback drift, but must
not replace deterministic acceptance tests or imply guaranteed semantic quality.

## Documentation, cost, and rollout

Update `CharacterAgent.md`, `Dispositional Traits.md`, endpoint background/failure
documentation, model-configuration execution descriptions, prompt module flow and
headers, and relevant synthesis documentation to describe the resulting order.
Update SDK documentation only where it describes changed workflow behavior; keep
public response schemas stable unless a demonstrated boundary need requires more.

For N nonempty bundles, this adds N - 1 normal synthesis calls compared with the
current end-only refresh. Synthesis is a sequential dependency between sources;
chunk parallelism remains available within a source. No new configuration flag or
database schema migration is proposed. Existing accepted agents and completed
drafts remain readable; existing portraits are not automatically regenerated.

Drain active jobs and deploy matching worker/API code and prompt/checkpoint
contracts. Resume compatible new checkpoints only; regenerate incompatible
intermediate work. Roll back code and contracts together, discarding incompatible
checkpoints. Preserve persisted IDs and previously committed source revisions.

Related: [CharacterAgent](CharacterAgent.md),
[Dispositional Traits](Dispositional%20Traits.md),
[Behavioral Identity Synthesis Plan](Behavioral%20Identity%20Synthesis%20Plan.md).

## Implementation record

- `source_evolution.py` owns five-scene partitioning, analysis merging, and candidate
  state reduction/synthesis shared by drafts and append. Existing scoring and
  lifecycle reducers remain authoritative.
- Draft checkpoint version 3 caches initial synthesis and complete source results
  plus their identities. Prompt version is `character-embodiment-v46-source-evolution`.
- Append validates identity before committing the timeline and portrait together.
- Synthesis uses active lifecycle entries and bounded perspectives. No public
  schema or database migration is required. Empty append operations do no LLM work.
- Deterministic tests cover sequencing, five-scene chunk parity, exact retry reuse,
  upstream invalidation, identity failure boundaries, and atomic transaction ownership.
  Verification: 415 CharacterAgent/configuration tests and 14 focused Python SDK
  tests passed. `git diff --check` passed. Tests use deterministic providers; live
  model quality/cost comparison and production Neo4j integration were not run.

Commands (run from their respective package directories):

```bash
# shrecknet/
SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-test-media ../.venv/bin/python -m pytest tests/test_character_*.py tests/test_embodiment_validation_config.py -q -rA
# python_sdk/
../.venv/bin/python -m pytest tests/test_character_agent_sdk.py tests/test_character_embodiment_sdk.py tests/test_character_profile_assignments_sdk.py tests/test_character_model_config_sdk.py -q
```

The media directory override is required in this environment because the configured
`/app` media location is not writable. Existing deprecation warnings remain.
