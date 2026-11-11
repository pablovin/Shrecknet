# ShreckWorld game engine: Phase 1 implementation plan

**Status: implemented; verification passed.** This plan translates the approved Phase 0 design into reviewable Shrecknet Core and ShrecknetFrontend work slices. The initial implementation includes additive registry/table persistence, ontology-scoped APIs, human approval/publication, durable placeholder runs, and the ShreckOS admin surface. It does not implement extraction, game mechanics, Companion delegation, Foundry synchronization, or ShreckRPG integration.

Phase 0 canonical design: `ShrecknetFrontend/documentations/architecture/SHRECKWORLD_GAME_ENGINE_PHASE_0.md` in the sibling frontend repository. This plan treats the approvals provided by the product owner as the decision baseline. If a local copy still shows stale `proposed`/`open` labels, reconcile the decision record before merging implementation; do not silently change the approved contract in code.

## Current implementation checkpoint

- **Backend slices 1–4:** implemented. Includes draft rulesets/definitions, ontology bindings, admin-only approve/reject/publish with review history, immutable snapshots, placeholder run task/status/retry/cancel, scoped ShreckTables, participant/Character Agent checks, archive, keyset list cursors, and revision-conditional rename.
- **ShreckOS slice 5:** implemented with an Apps launcher entry, World Builder-only app access, ontology picker, draft/publish/table/participant flows, and no-extraction run status copy in English, Italian, and Portuguese.
- **Slice 6:** Core API/architecture docs and frontend Apps/ShreckWorld docs document the implementation. Verification passed: Core ShreckWorld suite (180 passed, 1 skipped), ShreckOS production build, 48 focused ShreckOS tests, frontend root production build, and frontend lint (0 errors; existing warnings remain).

## Verified implementation context

Repository: Shrecknet Core, revision `41eb6328` when this plan was prepared. ShreckWorld is an independent FastAPI/SQLAlchemy service in `shreckworld/`; it owns a SQLite database and media. `app/db/session.py:create_schema` imports models, calls `Base.metadata.create_all`, then applies compatibility additions for existing installations. The `shreckworlds` backup bundle includes the service database/files and Qdrant embeddings (`Documentation/Deployment/BACKUPS.md`). Avoid reusing the `shreckworlds` model as a table or modifying its legacy source behavior.

Authentication is in `shreckworld/app/api/deps.py`: JWT/JWKS verification yields `ShrecknetPrincipal(user_id, role)`; `require_world_manager` accepts `admin` and `world_builder`. `shreckworld/app/integrations/ontologies.py` calls Shrecknet with the caller bearer token, validates ontology identity and maps upstream errors. This establishes identity and source-access building blocks, but does not by itself prove that `admin`/`world_builder` can administer every ontology. New APIs must use the authorized ontology dependency or equivalent per-request access check, fail closed, and never infer an ontology from a name.

Celery setup is in `shreckworld/app/celery_app.py`; tasks persist state in SQL, as shown by `app/tasks/agent.py`. The API router is registered in `app/main.py`; router aggregation currently starts in `app/api/routers/__init__.py`. ShreckWorld is documented in `Documentation/Architecture/SHRECKWORLD.md`, `Documentation/API/SHRECKWORLD_READER_API.md`, and `Documentation/Deployment/BACKUPS.md`. ShreckOS UI is maintained in the separate ShrecknetFrontend repository; the Core documentation states no frontend app is tracked here.

## Approved Phase 1 contract boundary

- Shrecknet Core owns ontology, user, and Character Agent identity. ShreckWorld owns ruleset definitions, version lifecycle, generation runs, and ShreckTable records/state.
- A table references exactly one existing integer `ontology_id` and exactly one immutable published ruleset version. Cross-database foreign keys and name-based matching are prohibited.
- Users and Character Agents are stable references, not copied profiles. Table participant association is data, not proof of authority; API handlers validate caller permissions and association ownership/access server-side.
- Phase 1 runs are durable placeholder jobs: they validate/persist a request and report that extraction has not happened. No model output is generated, reviewed, approved, published, or made executable in Phase 1.
- Published versions cannot be edited in place. Draft changes produce a new revision; publication pins exact definition/module versions. Existing rule review remains read-only and backward-compatible.
- New persistence is additive. Existing ShreckWorld sources, legacy `shreckworlds`, ontology IDs, route behavior, and ShreckRPG remain untouched.

## Delivery slices

Each slice is independently reviewable. Complete its backend tests and docs before starting dependent UI work. Keep domain decisions in services/repositories; routers map typed requests and errors, Celery tasks only orchestrate durable run execution, and UI remains in ShrecknetFrontend.

### 0. Baseline and contract freeze

**Owner:** Shrecknet Core maintainer with product owner. **Depends on:** approved Phase 0.

- Confirm target branch/current Core revision and compare current auth, ontology access, source IDs, job conventions, SQLite startup migration, backup and router behavior with this plan.
- Freeze the Phase 1 Pydantic/API schemas, role-to-capability mapping, status/error enums, table/version cardinality, retry/idempotency rules, and placeholder-run UX in the Phase 0 decision record.
- Record that `admin` and `world_builder` can administer only ontologies they are authorized to access; use existing Shrecknet access enforcement. Add explicit reviewer/publisher capability only if approved roles require it; do not infer privilege from a UI route.
- Confirm cross-repository API origin/auth configuration and ShreckOS Apps route/locale conventions with the frontend owner.

**Exit:** code-level references and frozen payload examples are recorded; tests can target concrete status and permission outcomes.

### 1. Typed GDM contracts and additive persistence

**Owner:** Shrecknet Core, `shreckworld/`. **Depends on:** slice 0.

Introduce focused modules following current package boundaries:

- `app/schemas/game_engine.py`: strict Pydantic request/response and definition payload envelopes (`extra="forbid"` at public boundaries), enums, cursor, error mapping contract, version and revision fields.
- `app/models/game_engine.py`: SQLAlchemy models for rulesets, bindings, definition revisions/definitions, published version snapshots/dependency pins, source-reference metadata, generation runs/attempts, tables, and participant/Companion reference associations. Use generated UUID strings for ShreckWorld IDs; keep ontology ID integer.
- `app/repositories/game_engine.py`: scoped CRUD, stable ordering, transactional run/idempotency operations, immutable version reads, and revision-conditional table updates.
- `app/services/game_engine/`: schema-version validation, ruleset revision/publication invariants, binding authorization orchestration, run lifecycle, and table/participant rules. Avoid a single catch-all service if separate responsibilities are clear.
- Extend `app/db/session.py:create_schema` to import the new models before `create_all`. New tables should be created additively; avoid rewriting or backfilling legacy rows. If a non-additive alteration becomes necessary, stop and provide an explicit migration/rollback design before implementation.

Persist one canonical JSON payload per typed definition with `kind`, `schema_version`, revision, provenance refs, namespaced permitted extension data, and validation status. Store published snapshots/digests and exact dependency pins so publication and table references remain immutable. Enforce uniqueness for `(principal_id, idempotency_key)` within run creation and stable IDs/constraints for associations. Index ontology, status, ruleset, version and owner lookup fields used by scoped endpoints.

**Tests:** fresh schema creation; upgrade against a fixture database containing legacy world/source rows; round-trip and invalid/unknown schema payloads; uniqueness; immutable published snapshot; dependency conflict; revision conflict; preservation of old APIs and rows.

**Exit:** Core can create a draft ruleset and publish a validated immutable version in service-level tests; no API/UI required yet.

### 2. Ontology binding and authorization

**Owner:** Shrecknet Core, `shreckworld/`. **Depends on:** slice 1.

- Add `POST /api/v1/game-engine/bindings` and scoped binding read/list endpoints from the frozen contract.
- Use the caller token with the existing Shrecknet ontology integration; validate ontology existence/access on every operation that requires it. Do not trust a client-provided role, world name, or prior frontend check.
- Define the boundary between “ontology exists” and “principal can administer this ontology” using the existing Core access contract. Treat `401/403/404` distinctly; upstream timeout/invalid response is safe retryable `503`, not an allow.
- Prevent duplicate binding conflicts according to the approved cardinality; rulesets remain reusable across different ontologies.
- Ensure ordinary reader roles cannot create/manage bindings, drafts, tables, or participant links.

**Tests:** allowed/denied role matrix; allowed/denied ontology membership; caller token forwarding; missing ontology; malformed upstream response; upstream 401/403/timeout; cross-ontology record isolation.

**Exit:** binding creation and list/read are fully scoped and have explicit error semantics.

### 3. Durable placeholder generation runs

**Owner:** Shrecknet Core, `shreckworld/`. **Depends on:** slices 1–2.

- Add typed run create, list, detail, retry, and cancel operations. Use opaque stable cursors ordered by `(created_at, id)` and bind filter/sort parameters to the cursor.
- Persist the idempotency key, authenticated owner, pinned ruleset revision, source revision/digest/locator references, status, attempts, safe diagnostics, request/correlation IDs, and timestamps before dispatching work.
- Dispatch a Celery task using a run ID only. The task reloads the persisted run, claims it once, validates that referenced inputs are structurally present and still accessible where required, and records a terminal placeholder result such as `execution_mode="placeholder", candidates_created=0, extraction_performed=false`. It does not call an LLM or generate definitions.
- Retries create a new linked attempt with the same pinned inputs and a new idempotency key; repeated identical create/retry keys return the original run. Terminal run history is immutable. Cancellation is best-effort and explicit.
- Define safe failure codes (`source_unavailable`, `source_changed`, `dispatch_failed`, etc.); never persist tokens, extracted passages, or arbitrary exception text for display.
- ShreckOS copy must say that the run is a queued setup/validation placeholder and no rule extraction has occurred. Do not show “generation complete” as if candidates exist.

**Tests:** duplicate create/retry; changed body with same key; worker duplicate delivery; worker restart/recovery; inaccessible/stale source; dispatch failure; retry of eligible failure; cancellation race; owner isolation; stable cursor pagination; safe diagnostic serialization.

**Exit:** users can create and observe durable placeholder run records and recover retryable failures without suggesting extraction occurred.

### 4. ShreckTable API and persistence

**Owner:** Shrecknet Core, `shreckworld/`. **Depends on:** slices 1–2; a published version must exist.

- Add create/list/detail/update/archive operations under `/api/v1/game-engine/tables`.
- Create requires a currently accessible ontology and a published ruleset version; persist stable ShreckWorld table ID, name, ontology ID, version ID, lifecycle status, timestamps, and optimistic `revision`.
- Update uses `If-Match`/revision. Reject stale writes with `409 revision_conflict`; reject changes to ontology/version pin in generic update. Pin changes require a future explicit, audited migration operation.
- Archive is idempotent soft archive; archived tables remain readable to authorized users and preserve associations/history, but reject new writes.
- Add participant and Companion reference association endpoints only for the approved Phase 1 contract. Validate principal/Character Agent identity and access through authoritative Shrecknet APIs when needed; store IDs and role/status only. Do not copy profile/lore or treat a link as delegated action capability.
- Scope every list/detail/mutation by ontology authorization. Use stable cursor pagination and return safe not-found behavior when disclosure itself would leak inaccessible resources.

**Tests:** version must be published; ontology/version pin cannot drift; table IDs unique; archive behavior and retry; stale revision; participant role checks; Character Agent mismatch/not found; cross-ontology isolation; upstream unavailable; pagination stability; preserve source APIs and legacy records.

**Exit:** authorized managers can create and maintain table records against an existing ontology and published version; no game mechanics execute.

### 5. ShreckOS Apps admin surface

**Owner:** ShrecknetFrontend/ShreckOS. **Depends on:** API slices 2–4 deployed to a compatible environment and frontend API types frozen.

- Add an authorized ShreckTable application entry to existing ShreckWorld Apps navigation. Follow existing role guards and localization architecture; add English, Italian, and Portuguese strings in the same change.
- Implement table list/create/detail/edit/archive and participant association flows. Ontology selection is from the caller-authorized ontology list; no display-name matching or hidden creation of a second ontology.
- Provide loading, empty, validation, permission-denied, stale-edit, archived, and upstream-unavailable states. Use TanStack Query patterns already established in ShreckOS and invalidate only affected server queries.
- Surface the pinned ruleset version and placeholder run status/limitations. Do not present placeholder runs as extracted/approved game data.
- Keep all endpoint authorization in the service; hide/disable navigation for unauthorized users only as a usability layer.

**Tests:** route/navigation role guards; list/create/detail/edit/archive; translation parity; keyboard/focus accessibility; loading/error/empty states; API 401/403/409/503 mapping; prevent double-submit/idempotency regressions.

**Exit:** authorized World Builders/Admins can manage tables through ShreckWorld Apps; non-authorized users cannot reach the UI or APIs.

### 6. Operations, docs, release and rollout

**Owner:** Core + frontend maintainers. **Depends on:** all implementation slices.

- Update Core canonical `Documentation/Architecture/SHRECKWORLD.md`, add `Documentation/API/SHRECKWORLD_GAME_ENGINE_API.md`, and update `Documentation/README.md`. Include payloads, statuses, permission matrix, error semantics, idempotency, archive/pin behavior, placeholder-run semantics and limits.
- Update Core `Documentation/Deployment/BACKUPS.md` only if the additional records require backup/restore behavior beyond inclusion in the existing ShreckWorld SQLite database bundle. Add restore checks for dangling ontology/version references; never reconcile by name.
- Update frontend canonical ShreckWorld Apps documentation, its documentation index, and `deploy/first-run/CHANGELOG.md` in the frontend repository. Record each user-facing/API feature in the single Unreleased section for that release.
- Rollout order: compatible schema/API backend first; verify health and migration; deploy ShreckOS UI second. Existing clients/routes remain functional. Rollback can remove/hide UI and disable new API routes while retaining additive tables/data; do not drop drafts or published versions.
- Monitor run dispatch age, failed attempts, safe diagnostic codes, upstream ontology lookup failures, and database growth. Define retention before production deployment; retain published versions and audit history.

## Cross-slice acceptance and verification

Core verification from repository root:

```bash
cd shreckworld
pytest
```

Run focused tests per slice first, then the full ShreckWorld suite. Frontend verification from ShrecknetFrontend root:

```bash
npm run lint
npm run build
```

End-to-end acceptance requires an authorized `admin` or `world_builder` who has access to an existing ontology to create a draft, observe a placeholder run that clearly reports no extraction, select a published ruleset version, create a ShreckTable, add/remove permitted participant references, edit with concurrency protection, and archive the table. A reader without management access is denied by both API and UI. Unavailable Shrecknet identity fails closed. Existing ontology/source reading and rule review continue unchanged. No ShreckRPG changes or dependencies are introduced.

## Dependency graph and merge boundaries

```text
0 Contract freeze
  └─ 1 Typed contracts + additive persistence
       ├─ 2 Ontology binding + access checks
       │    ├─ 3 Placeholder runs
       │    └─ 4 ShreckTable API
       │          └─ 5 ShreckOS admin UI (frontend repository)
       └─ 6 API/feature/operations docs + rollout (updated with each slice)
```

Slices 3 and 4 can be developed concurrently after slice 2 if they use the frozen shared schemas/repositories without overlapping model files. The UI depends on the API contract and may use mocked fixtures while backend slices are reviewed, but must not ship before compatible APIs. Every slice updates tests and canonical documentation together. No Phase 2 extraction work should be included in a Phase 1 PR.
