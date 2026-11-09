# Character Companion validation and deployment acceptance

## Repeatable package checks

Run Python checks from each package directory so its configured import path applies.
For local Shrecknet checks, use writable isolated `SHRECKNET_DATA_DIR` and
`SHRECKNET_MEDIA_ROOT`; a deployment config pointing to `/app` is unsuitable for a
host test process without that mount. The repository `.venv/bin/python` is used
relative to the package root as `../.venv/bin/python`.

- Shrecknet: `python -m pytest tests/test_character_companion.py tests/test_character_companion_import.py tests/test_character_agent_model_config.py tests/test_llm_status_operational.py -q`.
- Shared behavior: `python -m pytest tests/test_character_agent_decision_making.py tests/test_elder_query_v2.py -q`.
- Gateway: `python -m pytest tests/test_streaming.py -q` plus the package suite.
- SDK and ShreckWorld: `python -m pytest -q` from the respective package.
- RPG: `python -m pytest tests/test_chronicles.py tests/test_chronicle_search.py -q`.
- ShreckOS: `npm test -- --run`, `npm run lint`, `npm run build`, and the
  `character-companion.spec.ts` journey through both browser scripts.

Use an unused `SHRECKOS_E2E_PORT` and `CI=1` for browser checks when another app
already serves the default port; otherwise Playwright may reuse older application
code. Do not run independent Playwright invocations concurrently against the same
artifact directory.

## Verified coverage

Focused checks cover selection versions, user/character isolation, idempotency,
chat/turn cancellation races, delivered-message commitment, event replay, worker
redelivery, bounded signed rapport proposals, reset generations, deleted exchanges,
private image ownership/deletion, optional router failure, required web routing,
scoped evidence reuse/revalidation, reviewed read-only import and model-role
configuration/discovery. Existing CharacterAgent/Elder checks exercise the shared
memory/retrieval owners. These are deterministic tests with provider doubles.

The combined focused backend/config/shared-behavior checks (106 tests), gateway
configuration/streaming checks (12 tests), SDK suite (51 tests), ShreckWorld suite
(126 tests), Chronicle checks (15 tests) and ShreckOS unit suite (290 tests) passed
locally. The ShreckWorld/Chronicle counts are the earlier unchanged-package
checks; subsequent changes were verified in their affected packages. ShreckOS production build and
lint passed; lint reports 125 existing warnings. The Companion journeys cover desktop Chromium, mobile Chromium and Firefox,
including expired-cursor recovery without resubmission. Section navigation and
private image history have dedicated browser assertions. The focused checks passed
eight cases across desktop/mobile Chromium and four cases in Firefox. WebKit could not launch because
its host shared libraries are missing.

## Broader suite limitations

The Shrecknet full suite stops at three collection errors involving legacy
`architect_v2`, `OntologyInstance` and `_normalize_scene_ranges`; the same errors
reproduce on the unchanged baseline. The provider preflight test
`test_chat_polling_has_no_caller_generation_deadline` also fails on baseline
(expected no timeout, observed 60 seconds).

The gateway full suite retains failures in
`test_put_provider_rejects_empty_models_before_persisting` and
`test_put_provider_rejects_unknown_model_before_persisting`; these were also
observed on baseline. The RPG broad suite has failures outside Chronicle retrieval;
two sampled failures (resource persistence and combined backup filename) reproduce
on baseline. Its broad run was interrupted; no full-package pass is claimed. A wider Chronicle
browser run also had five failures in existing Atlas-reference/mobile-delete
journeys; a representative Atlas-reference failure reproduced on the unchanged
baseline. Focused new Companion and section-navigation journeys are checked
separately; no full browser-suite pass is claimed.

Docker Compose is now available. Its profile configuration validates, the backend
image builds, and the local API plus dedicated `companion` and `companion_rapport`
workers run against the actual SQL, Neo4j and Redis services. ShreckOS was rebuilt
and started with the matching frontend. Main/configuration SQLite backups were
created in the private data directory before service rollout. Legacy production
stores were not imported.

## Local provider smoke checks

The local OpenRouter provider was activated using its existing stored credential;
no key was copied into source or test reports. All four Companion roles use
`deepseek/deepseek-v4.1-flash`, remain editable in Administration, and preserve
independent reasoning settings. Companion is enabled locally with ShreckWorld and
Chronicle service URLs configured; web remains disabled without a search provider.

A disposable account exercised the actual API, outbox/Redis/Celery workers, canonical
Neo4j identity, OpenRouter streaming, image bytes, citations and asynchronous rapport.
The account, chats, events and attachment bytes were removed afterward. Existing
accounts, selections and canonical character visibility were not changed.

One warm-provider smoke round completed as follows:

| Path | First visible event | First text delta | Completion | Rapport |
| --- | --- | --- | --- | --- |
| Simple greeting | 43 ms | 1.65 s | 1.96 s | no_change |
| Own character memory | 57 ms | 1.97 s | 3.79 s | applied |
| Image input | 56 ms | 1.66 s | 1.97 s | applied |

The memory answer committed seven citation entries and the image answer one. A
subsequent image-history probe also verified the private reference in the user
message and completed in 4.98 seconds, with rapport applied.
These are smoke-test samples, not percentiles or performance guarantees. An earlier
cold text sample took roughly twenty seconds, and an earlier memory sample failed
without committing a final answer; subsequent memory runs completed. Provider
variability and semantic quality still need a larger evaluation set. Failure
snapshots now record stage/error type and safe validation rule identifiers without
retaining model output in error diagnostics.

## Deployment gates

Before a wider rollout, extend the local smoke checks to sustained concurrent
cancellation/reset/deletion, live source permission revocation and all configured
book/Chronicle datasets. Text/image streaming, memory citations and asynchronous
rapport have been exercised with the configured provider. Reconcile an import dry run against actual
legacy records and separately migrate attachment bytes if required.

Run `shrecknet/scripts/benchmark_character_companion.py` against configured models
and source services with representative simple/normal/complex/image cases. Record
first feedback, first token, completion, source failures, usage and costs when the
provider reports them. Proposed latency targets are not measured guarantees.
Curated human evaluation must assess character fidelity, semantic grounding,
uncertainty, source/image prompt injection resistance and rapport quality; schema
and citation-ID tests do not establish these qualities.

## Browser-session worker recovery (2026-10-23)

A local frontend turn failed while still queued because the Companion worker
inherited `SHRECKNET_BROWSER_SECURE_COOKIES=true` for an HTTP loopback origin.
The API overrode stale cookie settings, but the worker did not. The shared
Compose environment now applies the same public-origin-derived browser settings
to API and workers; see [browser authentication deployment](../../API/BROWSER_AUTHENTICATION.md#worker-cookie-mode-parity).
Both Companion workers were recreated with the corrected environment and current
API image. Their browser settings load successfully with loopback HTTP cookies.
No user messages or character state were rewritten.

The first session-backed probe then reached synthesis and found the configured
OpenRouter provider inactive. Re-enabling the existing provider with its existing
credential restored model access. A second disposable-account probe used an actual
SQL browser session (`sid` in its access token), selected an existing canonical
character, admitted a greeting through the HTTP API, and observed a completed turn
in 2.52 seconds. Probe accounts, sessions, and private Companion records were
removed afterward. This verifies session revalidation and the actual worker/model
path; it is not a latency percentile or a complete browser UI regression run.

`tests/test_browser_auth.py tests/test_character_companion.py -o addopts='' -q`
passed all 40 tests from `shrecknet/` with a writable temporary media/config root.
The first test command without `SHRECKNET_MEDIA_ROOT` could not collect because
its configured `/app` media directory is not writable on the host; rerunning with
`SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-browser-fix-media` and
`SHRECKNET_DATA_DIR=/tmp/shrecknet-browser-fix-config` passed. Rendered Compose
configuration confirms API, domain worker, and both Companion workers derive
cookie settings from the same public origin. No database migration is required.
Old failed turns remain historical failures; retry by sending a new message.

## Foundry contextual chat verification — 2026-11-06

- Module: `node --test tests/*.test.mjs` from
  `integrations/foundry/shrecknet-foundry/`: 84 passed. Includes header/context-menu
  signatures, shared compact/full lifecycle, account/cache isolation, immutable
  retries, fresh permission checks and attachment/source behavior.
- Backend: `python -m pytest -o addopts='' -q tests/test_companion_foundry_context.py
  tests/test_character_companion.py tests/test_companion_chat_sessions.py
  tests/test_foundry_integration_api.py tests/test_foundry_integration_service.py`
  from `shrecknet/`: 55 passed. Includes strict snapshot bounds, legacy text-action
  replay across upgrades, World isolation and a mocked-provider execution through
  final committed history. Three existing dependency/model-validator deprecation
  warnings remain.
- SDK: `python -m pytest -q` from `python_sdk/`: 62 passed. Snapshot forwarding and
  unchanged ordinary calls are included. The example was added but not submitted
  against a live account/provider.
- Native browser fixture `tests/foundry-context.browser.html`: five checks passed
  in both Chromium and Firefox, including inert HTML, hidden/secret filtering,
  labeled readonly values, revocation and draft/focus/scroll restoration. Three
  compact/full compiled-template layouts per browser passed overflow/composer
  checks at 360, 440 and 1080 pixels. These use stub Foundry documents.
- OpenAPI export and the additive `TurnCreate.foundry_context` schema were checked;
  `git diff --check` passed.

Backend checks used a temporary `SHRECKNET_DATA_DIR` and
`SHRECKNET_CONFIG_SEED_FILE` with a writable temporary `media_root`. The first run
against the local saved configuration failed on `/app` filesystem permissions;
production/local saved settings were not edited to run tests.

A broader `tests/test_*companion*.py` run plus the Foundry API/service files had
69 passes and two legacy `/users/me/companion` test failures in
`test_personal_companion_agents.py`: `test_orchestrator_queue_and_poll_lifecycle`
expects agents enabled, while the isolated configuration disables them; and
`test_orchestrator_queue_refreshes_allocated_tools` calls its seeding helper twice
with ontology ID 301, triggering a duplicate-primary-key error. These legacy
fixtures/configuration were not changed as part of contextual Character Companion.
The full Shrecknet suite was not run.

Real Foundry v14/custom-sheet integration, GM/player acceptance, provider answer
quality and production latency remain unverified. This feature-validation record
does not certify live table or backend deployment acceptance. Release packaging
and public asset availability are separate release checks.


## Evidence v2 acceptance

The added `tests/test_companion_query_pipeline.py` covers explicit/saved scope
inference bypass, Foundry explicit scope, original-request preservation, world
updates before retrieval, explicit-page conflicts and lasting overrides,
clarification, independent source failure, bounded targeted recovery, claim repair,
failed verification, structured snapshot support, cancellation with retained
commentary, default/invalid configuration, dedicated web targets and idempotent
SQL migration (including a leftover staging table). Planned-rule adapter tests
retain child coverage and omit oversized units whole. SDK tests also accept old
servers missing new model roles. These tests use provider/service doubles.

The focused backend/config/import run passes 162 tests; the full SDK run passes
68. Affected ShreckWorld planned-query/ontology/grounded-answer checks pass 28,
and gateway search checks pass 13. The Foundry Node suite passes 88. ShreckOS
passes 415 unit tests, production build and lint (85 existing warnings, no errors).
Companion browser checks pass 14 Chromium/mobile cases and 7 Firefox cases.
WebKit cannot launch on this host because system libraries including `libgtk-4`,
`libgraphene`, media codecs and GStreamer are missing. Run its browser script on
an appropriately provisioned host before claiming WebKit acceptance.

Use an isolated writable config seed when the developer's runtime config points
to container-only storage: `SHRECKNET_CONFIG_SEED_FILE` can name a temporary JSON
file and `SHRECKNET_DATA_DIR` an isolated temporary directory. Run from each
package directory; the SDK may need `PYTHONPATH=.` in an uninstalled checkout.

The existing deployed benchmark now records first stage/commentary feedback and
coverage/outcome as well as completion time. V2 first public reply is measured
from `response.final` because unverified factual deltas are intentionally absent.
No live-provider benchmark, production migration/concurrency load test, model
availability guarantee, cost distribution or curated human grounding acceptance
is implied by these local tests. Back up deployed SQL/config, drain workers, apply
the startup migration and deploy compatible API/workers/clients together. New or
missing pipeline settings default to `evidence_v2`; explicit saved selections persist. Retain compatible binaries for legacy rollback with durable skits.

## Rich response acceptance

The rich-response implementation has focused backend coverage in
`tests/test_companion_rich_response.py`: wire/schema/SDK synchronization,
deterministic projection, exact excerpts and physical/printed page separation,
zero/null fields, complete text fallback under table budgets, owner-only
history/final-event equivalence, invalid IDs, rejected blocks, optional media
failure, storage confinement, changed Atlas files/revisions, scoped Atlas
property definitions, explicit JSON web-image association and Foundry v1/v2
negotiation/idempotency. Tests use provider, graph and service doubles.

Run from `shrecknet/` with writable isolated data/media paths when installed
configuration points to container-only `/app` storage:

```bash
SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-rich-media SHRECKNET_DATA_DIR=/tmp/shrecknet-rich-test ../.venv/bin/pytest -q \
  tests/test_companion_rich_response.py tests/test_companion_query_pipeline.py \
  tests/test_companion_answer_details.py tests/test_companion_foundry_context.py \
  tests/test_companion_chat_sessions.py tests/test_character_companion.py \
  tests/test_character_companion_import.py tests/test_companion_index_coverage.py \
  tests/test_character_agent_model_config.py
```

The affected Shrecknet run passes 179 tests. ShreckWorld's complete suite passes
168 tests with one skip; the media tests
exercise protected revision-aware PNG generation, source deletion/inactivation,
foreign scope, grant restrictions, source-hash changes and unchanged embedding
metadata. The full SDK suite passes 70 tests (`PYTHONPATH=. ../.venv/bin/pytest -q`
from `python_sdk/`). The Foundry module passes 93 tests
(`node --test tests/*.test.mjs` from its package directory).

ShreckOS passes 418 unit tests and a production build. Lint passes with existing
legacy hook warnings and no errors. Its 32 Companion browser cases pass on
Chromium/mobile, including saved rich replies, all six types, row sorting,
lightbox/Escape, unavailable images, reload and unknown-version fallback.
Run with a free `SHRECKOS_E2E_PORT`, for example:

```bash
SHRECKOS_E2E_PORT=5187 npm run test:e2e -- tests/e2e/companion-rich-response.spec.ts tests/e2e/character-companion.spec.ts tests/e2e/companion-sessions.spec.ts tests/e2e/companion-experience.spec.ts
SHRECKOS_E2E_PORT=5188 npm run test:e2e:crossbrowser -- tests/e2e/companion-rich-response.spec.ts
```

The second command passes both Firefox cases but its two WebKit cases cannot
launch on this host because required system libraries (GTK/Graphene/GStreamer,
codecs and related dependencies) are missing. Run WebKit on a provisioned host.
No live model/provider benchmark, human image-subject evaluation, deployed source
CORS/authorization check or real Foundry v14 GM/player-sheet acceptance was run.
The [implementation record](Rich%20Response%20Plan.md#verification-and-delivery-gates)
keeps those production gates explicit. Rich responses default enabled for new/missing settings, preserving saved false values;
these local checks do not authorize a claim of live deployment acceptance.

## Retrieval execution verification (2026-11-09)

The retrieval execution change adds regression coverage for Atlas plan reuse and
scope validation, light/deep ShreckWorld payloads, conditional memory contracts,
automatic operational/evidence-gap Web recovery, public-query privacy, shared
recovery caps, saved timeout preservation, effective deadlines, cancellation and
lease heartbeats. Required-model stage windows were tested after a simulated
300-second elapsed interval, without waiting ten real minutes per unit test.
Bounded graph reads reject returned parents outside pinned ontology/instance scope.

Local Shrecknet checks used:

```bash
SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-companion-test-media \
SHRECKNET_DATA_DIR=/tmp/shrecknet-companion-tests \
../.venv/bin/python -m pytest <affected test files> -o addopts='' -q
```

Affected files: `test_companion_atlas_retrieval.py`,
`test_companion_index_coverage.py`, `test_companion_query_pipeline.py`,
`test_companion_recovery.py`, `test_elder_query_v2.py`,
`test_hybrid_neo4j_retriever.py`, `test_companion_answer_details.py`,
`test_companion_chat_sessions.py`, `test_companion_rich_response.py`,
`test_companion_foundry_context.py`, `test_character_agent_model_config.py`,
`test_llm_status_operational.py`, and `test_celery_startup_cleanup.py`.
The broad run passed 197 checks after deselecting the seven baseline failures
below. The subsequent shared-retriever/Atlas run passed all ten checks, including
three additional scope/embedding-deadline regressions (200 distinct passing checks).

These failures also reproduce in an isolated baseline copy, without the retrieval
execution implementation; they remain outside this change:

- `test_companion_rich_response.py::test_presentation_enablement_cannot_change_legacy_pipeline_behavior`
- `test_companion_foundry_context.py::test_admission_freezes_snapshot_preserves_identity_and_replays`
- `test_companion_foundry_context.py::test_router_does_not_search_other_campaigns_when_foundry_scope_missing`
- `test_companion_foundry_context.py::test_snapshot_reaches_synthesis_and_committed_history`
- `test_character_agent_model_config.py::test_companion_pipeline_defaults_to_evidence_v2_for_missing_setting`
- `test_character_agent_model_config.py::test_companion_pipeline_keeps_explicit_legacy_selection`
- `test_character_agent_model_config.py::test_companion_initial_seed_selects_evidence_v2`

They reference retired legacy routing/pipeline settings or outdated test doubles.
The baseline admission reproduction retained the pre-existing working-tree web
rate-limit change. No retired runtime branch was restored to satisfy those tests.

Additional checks passed from their package directories:

- ShreckWorld: `../.venv/bin/python -m pytest tests/test_query_v3.py tests/test_ontology_items.py tests/test_grounded_answer.py -o addopts='' -q` — 29 passed.
- shreckLLM: `../.venv/bin/python -m pytest tests/test_config_store.py tests/test_streaming.py tests/test_api_integration.py -q` — all 50 selected checks passed; provider migrations preserve saved generation/hedge limits.
- SDK: `../.venv/bin/python -m pytest tests/test_character_companion_sdk.py -q` — 8 passed; raw turn/history reads preserve additive diagnostics.

Initial local collection failed because deployment media settings pointed into
`/app`; setting the two `SHRECKNET_*` paths above resolved it. These checks do not
constitute a full repository test run. Real provider/gateway/proxy ten-minute
smoke tests, worker-concurrency/load tests, browser journeys and curated semantic
memory/relevance/grounding evaluation were not run. They remain deployment gates.
The [startup configuration migrations](./Model%20Configuration.md#ten-minute-call-rollout)
now upgrade shorter saved timeouts automatically; no schema migration or
re-embedding is needed.

## Automatic timeout configuration migration verification (2026-11-09)

The one-time startup migrations supersede the original opt-in saved-timeout policy.
They raise shorter Shrecknet call/round/turn limits and gateway generation/hedge
limits, retain longer valid budgets, align the watchdog and Companion hedge routes,
and leave subsequent administrator edits unchanged. Each configuration database
records its own marker in the same transaction as its changes.

New focused tests exercise legacy partial/full configurations, fresh databases and
short bootstrap seeds, longer custom limits, idempotency, subsequent edits/reload,
concurrent migration attempts, rollback after an injected SQLite write failure,
and retry after that failure. Configuration controls unrelated to timeout upgrades
remain unchanged. No live deployment database was edited during these tests.

Checks run from each package directory:

- Shrecknet: `../.venv/bin/python -m pytest tests/test_companion_timeout_migration.py tests/test_companion_query_pipeline.py tests/test_character_agent_model_config.py -o addopts='' -q`, with the three previously reproduced retired-pipeline configuration tests listed above deselected — 115 passed, 3 deselected. Use the writable `SHRECKNET_*` test paths above.
- shreckLLM: `../.venv/bin/python -m pytest tests/test_config_store.py tests/test_api_integration.py tests/test_streaming.py -o addopts='' -q` — 55 passed.

The production startup/reload procedure, external provider behavior, browser tests
and load benchmarks were not run for this migration. Back up both configuration
stores, drain turns and restart/reload Shrecknet API/workers and the gateway
before verifying their configuration endpoints. ShreckWorld's explicit environment
overrides remain outside the configuration-database migration.

## Compact assessment and evidence preservation verification (2026-11-09)

The assessment recovery checks cover injected timeout/provider/JSON/schema/support
failures and an actual short asynchronous deadline. Successful compact recovery
resumes synthesis and final verification. Persistent failures retain full source
references and render only attributed extracts, with partial coverage and explicit
limitations. Tests also cover failed reassessment with earlier validated support,
final-verifier failure after recovery, empty evidence, malicious source markers/
HTML, output/collection/retry omissions, source revocation, saved owner-only
Answer Breakdown diagnostics, and unchanged compact evidence budgets.

Readiness checks cover mixed indexed/unindexed/incompatible books, independent
single/planned source subsets, scoped attention IDs, continued use of active
indexes during queued/failed replacements, and compatible generations with no
chunks. Counts do not overlap. The typed SDK retains attention details and raw
Companion result/history diagnostics. Full navigation geometry/hashes remain
available in retained evidence but do not enter the model evidence projection.

Commands run from their package directories:

- Shrecknet: `../.venv/bin/python -m pytest tests/test_companion_assessment_recovery.py tests/test_companion_query_pipeline.py tests/test_companion_index_coverage.py tests/test_companion_answer_details.py tests/test_companion_rich_response.py tests/test_companion_chat_sessions.py tests/test_companion_atlas_retrieval.py tests/test_companion_recovery.py tests/test_companion_timeout_migration.py tests/test_character_agent_model_config.py -o addopts='' -q` — 178 passed, with the previously documented legacy presentation/config tests deselected (4). Use the writable `SHRECKNET_*` environment paths above.
- ShreckWorld: `../.venv/bin/python -m pytest tests/test_structured_retrieval.py tests/test_query_v3.py tests/test_ontology_items.py tests/test_world_reader_api.py tests/test_grounded_answer.py -o addopts='' -q` — 57 passed.
- SDK: `../.venv/bin/python -m pytest tests/test_shreckworld_reader.py tests/test_character_companion_sdk.py -o addopts='' -q` — 15 passed.

A subsequent focused run of `tests/test_companion_assessment_recovery.py` passed
all 20 checks, including the additional guard against reusing previously
`unverified`/contradicted quotes as verified support and a smaller compact-retry
prompt-size assertion. These are local affected-suite checks, not a full repository run. Live provider/
gateway latency and ten-minute deadlines, load behavior and browser journeys were
not exercised. The existing Answer Breakdown is preserved through additive
schemas, full evidence/navigation references, excerpts and warning/count fields;
no frontend implementation was changed. Deploy the ShreckWorld publisher and
Companion workers together for per-book attention details. This change requires
no SQL schema migration or re-embedding. Existing timeout migration markers and
600-second primary assessment limits remain; compact retries add at most sixty
seconds per assessment invocation within the turn budget.
