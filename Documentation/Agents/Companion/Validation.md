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
