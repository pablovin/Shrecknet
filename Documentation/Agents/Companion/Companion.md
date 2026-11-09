# Character Companion

Character Companion lets an authenticated user select an existing canonical
CharacterAgent and converse with it across Atlas, ShreckWorld, and Chronicles.
The legacy `/users/me/companion` personal persona API remains unchanged.
The feature is disabled by default. Model roles default to OpenRouter
`deepseek/deepseek-v4.1-flash` and remain independently editable in Administration.
The provider needs configured credentials and activation before conversation.

## Contract and invariants

Selection is per user and versioned. Chats bind permanently to one user and one
character; changing selection preserves earlier chats and rapport. Non-admin users
can select active public characters. Identity readiness is checked at admission
and execution. Identity uses the canonical identity, psychological and personality
summaries plus effective personality traits; Companion never edits those records.

Rapport belongs to the user/character pair across chats. Defaults are connection
25, coordination 20, trust 20, familiarity 0, openness 10 and current_strain 0.
All scores stay within 0..100. Only completed exchanges enter asynchronous rapport
evaluation, in completion order. Proposals require exact message quotations and
confidence >=0.6; changes are bounded to +/-2, or +/-5 for current_strain. Reset
increments the generation so an older proposal cannot overwrite reset scores.
Repeated evaluation failures settle as audited no-ops after the configured attempts.
Deleting a chat preserves pair scores, removes its private content asynchronously,
and settles outstanding evaluations without using deleted conversation content.

## Execution and ownership

`character_companion` routes map HTTP to `CharacterCompanionService` and
`CompanionRepository`. SQL stores selections, pairs, chats, messages, turns, events,
private attachment metadata, audit records, idempotency records and a durable outbox.
Neo4j remains the authority for CharacterAgent identity and memory.

The historical legacy worker flow (no active runtime selector) was:

1. Recheck originating user/session and canonical identity; load pair scores and
   up to twelve committed messages from this chat.
2. Stage 1 deterministic routing, optionally assisted by the routing model for
   ambiguous requests. Optional routing failure falls back to conversation.
3. For image turns requiring retrieval, interpret the image into bounded visible
   observations and search terms. Interpretation is not published as an answer.
4. Retrieve allowed sources concurrently with independent tool timeouts and an
   overall deadline. When the synthesis target enables provider search,
   `WEB_RETRIEVAL` receives at most the first 500 characters of the submitted
   question, without added Foundry names or image-derived terms; provider citations
   with source excerpts become external-web evidence. Its generated summary is not evidence.
   An optional interim model can react concurrently using only
   identity and the question. Its output is provisional and excluded from rapport.
   Provider-managed search is disabled for routing, interim reactions and rapport
   evaluation, even when enabled on those model targets.
5. Stage 2 streams one embodied prose answer with provider search disabled;
   image interpretation also disables provider search. Text deltas are provisional.
   The backend removes optional inline source markers, builds the response envelope
   and accepts only markers for supplied evidence. It rechecks current source
   access/revisions, the originating session and character availability before commit.
6. Commit the delivered user/assistant exchange, final event and rapport outbox in
   one transaction. Stage 3 evaluates rapport separately with version/generation
   checks and an audit trail.

Prompt contracts live beside the job in `jobs/companion/prompts.py`. Only Stage 2
produces final public output. Deterministic marker resolution enforces reference integrity; it does not prove
semantic grounding, claim classification or character fidelity.

The final result has one `conversation` segment representing the whole answer;
its cited evidence is model-declared support for the answer, not a verified
claim-by-claim attribution. Unknown complete source markers are removed and
reported in `warnings`; missing markers do not fail an answer. `coverage` is
`unknown` unless a retrieval failure or partial Foundry snapshot makes it
`partial`. Empty output, provider errors, cancellation and authorization or
source revalidation failures can still prevent completion.

Atlas uses the shared Elder retrieval executor without Elder synthesis. Character
memory uses the shared CharacterAgent ranking service and only the selected
character's perspectives. Memory is subjective evidence, not objective scene truth.
ShreckWorld uses evidence-only book retrieval. Chronicle search filters authorized
notes before ranking and uses text offsets/revision references; ShreckOS opens the
referenced body/reply passage and warns when the revision has changed. Web search
uses a configured provider; it does not fetch arbitrary result pages. The question
itself is not automatically redacted or converted to a public-only query, so users
should not include private information in questions because web search may be
considered on any turn. The router still decides whether a turn actually calls web.
Evidence is capped at twenty records and the configured estimated token budget.
The final context also has a bounded token estimate; model-specific context-window
fit and image token costs still require deployment validation.

ShreckWorld reports the number of sources searched, not indexed, and requiring
reindexing for the active embedding fingerprint. The Companion preserves those
counts and passes partial-search outcomes to synthesis; compatible passages can
still be used. Index readiness is independent of call success: returned passages
keep `status="ok"`; no passages produce `status="empty"`, with partial-readiness
diagnostics when requested indexes are missing. Service failures remain
`unavailable`/`timed_out`. An incomplete index is never evidence of absence. Reindex affected sources with ShreckWorld's existing indexing workflow.
Rules evidence is not reused from the prior turn because the active fingerprint
or source availability may have changed.

## Evidence-first query pipeline

The evidence-first pipeline is the sole runtime path. The retired
`companion_query_pipeline_version` and optional-routing flags are not configuration
controls; selecting a legacy value does not activate another runner.
Six independently configured model roles
cover routing, dedicated web search, evidence review, synthesis, commentary and
rapport. All inherit the repository's OpenRouter DeepSeek Flash model default;
only the dedicated web target defaults to `web_search_enabled=true`.

1. **Scope fast path.** Validate an explicit page/Foundry ontology or reuse the
   chat's saved ontology, skipping the ontology-inference call. Missing scope is
   inferred using the routing model and bounded authenticated ontology discovery.
   Unresolved scope produces a direct question by world name. The character's
   ontology is never substituted for an unresolved conversational world.
2. **Scope and continuation decisions.** For an existing world, the routing
   model's separate `SCOPE_INTENT` stage distinguishes a requested switch from a
   comparison and a pending clarification answer from a new topic. Missing-world
   resolution carries the same continuation decision. Only relevant answers resume
   the saved goal; new topics abandon it. The backend validates world names,
   clarifies page conflicts, clears incompatible source bindings and persists an
   authorized override before research planning. Identity and rapport do not change.
3. **Simple research planning and compilation.** `SimpleCompanionPlan` exposes only
   `mode` (`search`/`chat`) and up to three `tasks`, each with a nonblank question
   of at most 500 characters and one to three available internal `sources`. Web
   is excluded from the planner schema and rejected by the compiler. Search requires
   tasks; chat requires an empty list. `planning.py` merges repeated questions and
   sources, generates stable need/call IDs, filters unavailable sources and assigns
   light retrieval. Up to nine initial calls honor all three-by-three requests.
   Tasks losing their sources use permitted local alternatives. Technical planning
   failures search the original resolved goal using authorized ShreckWorld/Atlas;
   they never manufacture clarification or factual chat. Long fallback questions
   retain their full goal and split into deterministic 4,000-character queries,
   with deferred segments audited. Without local tools, the unmet research goal
   still reaches assessment; Web cannot run before a valid insufficient decision.
4. **Independent parallel retrieval.** Atlas executes a supplied, scoped Elder
   `RetrievalPlan` or deterministically converts the Companion's individual query
   into one `hybrid_search` step. Ordinary calls make no second planning-model
   request. An explicit deep Atlas call without a reusable plan can invoke Elder
   planning. Invalid supplied plans fail without replanning. ShreckWorld defaults
   to `retrieval_strategy="single"`, `mode="evidence"` for individual questions;
   explicit deep calls retain `planned` retrieval and its child coverage trace.
   Neither source produces a second public answer. The dedicated web model uses
   gateway search and actual provider excerpts; generated summaries are not
   evidence. JSON web search remains an explicitly configured alternative.
   Failures/timeouts preserve successful siblings. Source scope, revision checks,
   grants and evidence budgets remain enforced; earlier-turn evidence is not reused.
5. **Assessment and recovery.** One assessment reasons over at most eight
   need-balanced internal excerpts. `EvidenceAssessment` returns exactly
   `{"sufficient":false,"web_queries":[]}`: a strict Boolean and at most two
   nonblank queries of 1–500 characters. A sufficient response discards queries;
   an insufficient response with no queries is valid for private-only gaps or
   information no safe public lookup could verify. Invalid output/provider failure
   gets one retry with identical evidence/needs/outcomes, bounded to 60 seconds.
   A valid insufficient response may trigger one Web batch, at most two queries
   and five unique Web records total. Local failures alone never trigger Web.
   Merge Web snippets with internal evidence and synthesize without reassessment.
   If both assessment attempts fail, skip Web and attempt grounded synthesis with
   explicit uncertainty. If synthesis/verification fails, use attributed extracts.
6. **Private synthesis and claim verification.** Synthesis uses identity, traits,
   subjective memories, rapport and evidence to produce typed segments. Every
   segment is reviewed, including reactions that might disguise factual claims.
   Exact quotations and known source IDs are also checked deterministically.
   Unsupported output gets at most one repair; rejected segments are omitted or
   replaced with a limitations reply. No unverified factual draft is streamed.
   Uncited personal reactions are allowed. Semantic review remains model-assisted
   and requires curated human acceptance evaluation.
7. **Final commit.** Revalidate identity, session and all supplied source access/
   revisions before committing the final answer and rapport outbox. Operational
   clarification/switch-only turns settle rapport in order without score changes.

Personal memory is not an automatic retrieval prerequisite. A proposed memory task triggers
a separate `MEMORY_RELEVANCE` check against the original resolved user intent, not
the model-generated subquestion. Only a positive decision permits retrieval; gate
failures skip memory. The compiler supplies the internal `memory_reason`. Generic rule explanations,
public facts, greetings and style requests normally skip memory. Canonical identity,
effective traits and rapport still shape every answer. Memory retains selected-character
and public/private access restrictions and remains subjective evidence.

Safe public-query generation belongs to `ASSESS`, with the former
`PUBLIC_RECOVERY` privacy rules included in its self-contained contract. There is
no separate public-query rendering call. Only public system/edition, published
rules/lore and public facts may enter Web queries. Private campaign canon cannot
be repaired with Web. Backend bounds, normalization/deduplication and known private
name/ID checks provide additional controls; string matching is not proof of semantic
privacy. Known private identifiers are rejected without a raw-question fallback.
`web_recovery_blocked` records `privacy_blocked` or `no_public_queries` when
applicable. Configured Web availability remains the permission authority; legacy
`tools.web` and the initial internal-tool allowlist do not disable configured Web
recovery. `companion_recovery_max_rounds=0` disables Web recovery.

`PlannedCall` retains `retrieval_depth`, `depth_reason`, nullable `atlas_plan` and
nullable `memory_reason` for internal execution and adapters. The assessment has
no source IDs, support objects, per-need statuses, confidence or follow-up tasks.
The backend generates recovery call IDs and associates queries with planned needs
as retrieval intent, never as proof of support. No deep local recovery is selected
by assessment. Prompt contracts live beside the job in `query_prompts.py` and
`query_contracts.py`.

Ordered flow: scope resolution/continuation → backend scope update → internal
research planning → optional memory relevance → deterministic compilation →
optional image interpretation → parallel internal retrieval/commentary →
eight-excerpt assessment → optional identical-context retry → optional single Web
batch → grounded synthesis → verification/one repair → backend source checks and
final commit. Rapport follows asynchronously. See the
[assessment design](./Assessment%20Recovery%20Plan.md) for ownership and regression
acceptance. `sufficient` is a pre-Web decision, not final verification. Recovery
and assessment-outage answers retain `coverage="partial"`; completion alone never
upgrades coverage. Uncertainty/clarification/general-knowledge segments in research
answers also prevent complete coverage.

Per-call retrieval, routing, evidence review and synthesis limits default to
600 seconds. Retrieval rounds default to 615 seconds and the total turn to
10,200 seconds to accommodate sequential stages, recovery and repair. These are
maximum budgets, not expected latency or correctness guarantees. Optional media
keeps its separate two-second budget and textual fallback. A one-time startup
migration raises shorter saved timeout limits; later admin edits are preserved. See [timeout rollout](./Model%20Configuration.md#ten-minute-call-rollout).
An outbox heartbeat runs every fifteen seconds, separately from network work;
workers missing heartbeats for ninety seconds can be recovered. Cancellation and
lease loss fence ongoing work and final writes. Broker visibility outlives a turn.
Originating authentication expiry and source revocation still end access; fresh
short-lived grants are minted for each downstream request and source check.

General-knowledge fallback defaults to disabled. With administrator policy
`labeled_opt_in`, a turn must also set `general_knowledge_opt_in=true`. Such text
is labeled **General knowledge — not verified against your world or books** and
cannot represent verified canon or exact rule text. Web search availability depends
on backend provider configuration, not on a per-turn client permission.

An acknowledgement is persisted during admission. `pipeline.status` events report
resolution/continuation, scope updates, research planning/compilation, retrieval, assessment/recovery, synthesis
and factual verification. Optional character commentary runs concurrently; delivered
commentary remains ordinary owner-only transcript history after finalization,
failure, cancellation, reload and event-log expiry. Commentary is excluded from
completed-exchange context, factual evidence and rapport quotations. Snapshots
retain the current stage and commentary; ShreckOS uses SSE and Foundry polls them.

Chat JSON records `resolved_ontology` (including resolution source and ontology
revision), `scope_override` and pending clarification.
Turns preserve the original submitted request separately from the validated
`execution_context` used by retrieval grants. Message `kind` and `sequence`
distinguish user, commentary and final records. Startup runs the idempotent
`migrate_companion_query` migration, replacing the old message uniqueness rule.
Back up SQL/config and drain workers before upgrading. Startup `create_all` alone
is insufficient. No graph migration or re-embedding is required. A rollback
requires a compatible release whose context/rapport reads exclude commentary;
there is no runtime legacy-pipeline selector. Older binaries assume one assistant
message per turn. Keep additive columns and delivered messages intact.

#### Pipeline diagnostics

The companion worker emits bounded, structured `companion.diag` INFO lines to the
console for troubleshooting: `stage` for every pipeline transition,
`router_available_tools` (candidate tools after configuration filters),
`router_response` (the validated plan),
`ontology_response`, and per-call `tool_response` (tool, status, reason and
evidence). Retrieval adapter failures log the exception type and message instead
of only the class name. Use these lines to distinguish a planner omission from an
unavailable/unauthorized source; they are console diagnostics and do not change
the turn snapshot, `pipeline.status` events or stored error details.

See [the design and acceptance plan](Query%20Pipeline%20Plan.md),
[configuration](Model%20Configuration.md) and [HTTP/SSE additions](Frontend%20API.md#evidence-v2).

## Recovery, privacy and operations

Admission requires a UUID `Idempotency-Key`. Reusing the key with another payload
returns 409; reconnects read events or snapshots without submitting another turn.
At most one turn executes per chat and two per user. Drafts are never committed
assistant messages. Cancellation fences late worker writes. A stale turn worker
is interrupted rather than silently regenerating an already streamed response.
Admission schedules an immediate best-effort outbox dispatch after sending the
202 response. SQL outbox recovery tolerates broker outages; dispatch also runs every
five seconds, so a failed immediate publish cannot lose an admitted turn. Failure
snapshots retain safe stage/error-type/validation-rule diagnostics, never raw model
output in error details.

SSE events are private and retained for seven days by default. Expired cursors
return 410; clients recover from the turn snapshot. Account streams periodically
recheck authentication. Short-lived retrieval grants use a separate token audience,
bind user/session/turn/tool/scope, and are accepted only by retrieval authorization.
Raw browser bearer credentials are not persisted in turn inputs.

Images are owner-only, normalized PNGs outside public media storage: four per
turn, 10 MiB per upload, 20 MiB uploaded aggregate per turn, at most 20 megapixels.
Unattached images expire after 24 hours. Deleted bytes are removed and metadata
tombstoned; deleting an account also queues private-byte cleanup. Admitted user
messages retain private attachment references for history. ShreckOS loads previews
only when Sources is expanded, via authenticated bytes and temporary blob URLs.

Back up the main SQL database and private data directory before rollout. Existing
startup `metadata.create_all` adds Companion tables; it does not migrate a deployed
older revision of these new tables. Stop workers before restoring backups. Rollback
by disabling `companion_enabled`, draining workers and restoring the prior images;
leave additive tables intact unless a separately reviewed cleanup is required.

The standard Compose startup includes `shrecknet_companion_worker` and
`shrecknet_companion_rapport_worker`. Configure synthesis and
rapport models before enabling both `enable_ai_agents` and `companion_enabled`.
Routing/interim models are optional and have separate enable switches. See
[model configuration](Model%20Configuration.md).

Shrecknet API startup deletes persisted background job history and purges its
registered Celery queues and reserved messages before the Compose workers start.
Before dispatch starts, pending or running Companion turns are marked
`interrupted` with retryable `server_restarted` errors and their chats are unlocked.
Pending or running rapport evaluations settle in completion order as audited
`failed` no-ops, advancing the pair cursor without changing scores or calling a
model. Privacy cleanup outbox entries remain durable and are dispatched normally.
A worker restart alone does not reset queues. Each worker attempts embedding warmup when enabled;
warmup failures are logged and do not prevent startup.

The read-only legacy importer defaults to dry run and requires a reviewed user-ID
mapping plus `--ownership-reviewed --apply` to write. Run from `shrecknet/` with
`PYTHONPATH=. python scripts/import_character_companions.py --help`. It never starts
legacy startup migration code. Review quarantined mappings and source checksums;
legacy attachment bytes need separate migration. Historical imports preserve legacy `ts` timestamps and `meta` fields, keep interim
messages separate, and do not trigger rapport evaluation. Do not delete the source installation until reconciliation.

## Deployment acceptance still required

Run the deployed benchmark script with representative providers and real source
services before claiming latency or quality targets. Provider image support must
be explicitly allowlisted; unknown capability is not assumed. Local provider smoke results are recorded in [validation](Validation.md).
No production latency distribution, cost, sustained live database concurrency or
production import acceptance is implied by unit tests or a small smoke sample. Semantic grounding, character fidelity and rapport quality require curated
human evaluation. Same-chat evidence from the latest completed turn can be reused for an identical
question or recognized short follow-up for five minutes, only with identical scope
and current-source validation. Revocation, revision changes or scope changes cause
a cache miss; sources are revalidated again before final commit. Historical conversation text is the owner's transcript,
not a promise of continued access to the original source document.

## Related contracts

- [Query pipeline redesign plan](Query%20Pipeline%20Plan.md) — design and rollout acceptance baseline
- [Validation and deployment acceptance](Validation.md)
- [Frontend HTTP and SSE API](Frontend%20API.md)
- [Companion model configuration](Model%20Configuration.md)
- [Design and acceptance plan](Companion%20Backend%20Plan.md)
- [Python SDK](../../../python_sdk/docs/character_companion.md)


## Chat session limits and context

Each user can keep ten non-deleted Companion chats across all characters. Existing
accounts above ten retain their history; only creation is blocked. Chat creation
serializes the quota check and insertion across workers. An exact idempotency
replay returns the saved chat even at the limit; a new eleventh chat returns
`409` with `detail.code: chat_limit_reached`. Chat titles are trimmed and must
contain 1–255 characters. Duplicate titles are allowed.

`GET /chats` retains `items` and `next_cursor` and adds
`usage: {count, limit, remaining}`. Usage always counts all characters, including
when `character_agent_id` filters the list. `GET /capabilities` adds
`chat_sessions: {max_chats: 10, limit_scope: "user"}`. SDK `chats()` returns these
fields without transformation. All existing routes and SDK methods remain valid.

Delete returns 202 after logical deletion commits and frees one slot. Physical
purge continues through the existing outbox. A running turn returns
`409 chat_busy`; cancel it before retrying deletion. Admission and logical deletion
use conditional chat writes so a deleted chat cannot accept a new turn. Deleted
chat content is excluded from account event replay. `chat.created`, `chat.updated`
and `chat.deleted` account events invalidate session lists; deletion events contain
only the chat ID and have no chat-scoped content. Rapport and character memory
are independent of chat deletion.

Conversation context loads only completed, committed exchanges for the addressed
owner/chat. It includes at most six recent complete exchanges and 48,000 text
characters, then drops complete oldest exchanges as needed for the existing
synthesis prompt budget. It never slices individual messages or includes failed
assistant drafts. New user input enters the prompt separately, exactly once.
Older history remains available in message pagination; automatic summarization
is not implemented. Canonical character identity, pair rapport and character memory
remain separate inputs, so starting a new chat does not reset those inputs.

Explicit per-turn `context` takes precedence over `default_scope`; null/omission
falls back to default scope, then global. `{surface: "global"}` explicitly disables
active-page grounding while retaining character identity and authorized retrieval.
Page IDs never grant access. Source authorization and retrieval policies remain
unchanged; this does not introduce retroactive redaction of historical text.

Deploy the backend before the updated frontend. No database migration is required.
Rolling the frontend back retains chats and backend quota enforcement; removing
quota enforcement requires a backend rollback. Never delete excess legacy chats.

## Foundry contextual conversations

Foundry uses this same Character Companion pipeline with optional typed
`foundry_context` snapshots. `companion_foundry_context.py` owns Shrecknet
World/ontology scope validation and conversion to user-provided evidence;
`CharacterCompanionService` admits an immutable snapshot and saves its manifest.
Before Stage 1, the worker constructs bounded snapshot evidence without another
model call. The router receives table/system titles and attached document names
as context, and retrieval queries include bounded document/system names. No
backend-to-Foundry live lookup occurs. Stage 2 receives those sources alongside
ordinary retrieval evidence. Foundry facts remain client-supplied observations,
not verified rules or authority to change the speaking identity. Existing citation
validation applies, and partial snapshot coverage keeps the answer partial.
Stage 3 continues to evaluate only delivered conversation quotations; it does not
use the snapshot manifest as relational evidence or modify canonical identity.

A selected table World never silently falls back to the character's unrelated
ontology. Atlas scope is the explicit member ontology, or the sole member; an
ambiguous collection disables Atlas routing. Character memory retains its original
character scope. Chronicle routing requires an explicit Chronicle ID. Existing
ShreckWorld grants still require explicit rules-library scope and authorization.
Snapshot turns bypass same-chat retrieval reuse so old game data cannot select a
cached source bundle. Document extraction/permissions occur in the player's
Foundry browser; the backend cannot attest their truth. Image plus snapshot data
is checked against the configured evidence budget before synthesis.

The additive field uses existing JSON storage and owner-only history/purge paths.
Deploy backend and worker support before the updated module. Older servers retain
text chat with contextual controls unavailable. Keep additive evidence decoding
when rolling a module back. See the [Foundry guide](../../Deployment/FOUNDRY_MODULE.md#contextual-chat-and-compact-sidecar)
and [snapshot API](Frontend%20API.md#foundry-context-snapshots) for the current
contract, bounds, trust limits and remaining live Foundry acceptance checks.

## Inspecting answer origins

ShreckOS provides an expandable **Answer breakdown** on new committed replies.
It lists retrieval tools and their outcomes (including empty, timed-out and
unavailable tools), cache reuse, requested queries, and returned versus supplied
record counts. Tool evidence includes excerpts and inspectable source references
and structured data. Answer passages show their declared evidence links; passages
without links are explicit. Attached context is listed separately from tool calls.
All supplied sources are validated before commit, including uncited evidence.
The transcript reconstructs this view from existing turn JSON; older replies have
no fabricated tool trace and retain their existing Sources view. No database
migration is required. See the [HTTP contract](Frontend%20API.md#answer-tool-and-evidence-breakdown)
for privacy, source-access limits and rollout order.

### Ontology-owned ShreckWorld items

A ShreckWorld page context can carry `surface: "shreckworld"`, `ontology_id`, and optional `library_item_ids` directly. It does not require a separately created knowledge world. New clients submit direct ontology scope. Legacy contexts can retain `shreckworld_id` alongside their Atlas ontology; the explicit legacy ID keeps its original source scope. The server may enrich an admitted turn with its character ontology. Direct evidence and citation validation use `/ontologies/{id}/companion-evidence` and `/companion-validate` with the existing turn-bound retrieval grant, pinned to the admitted ontology and optional source subset. Legacy scopes retain their original routes. Evidence cards carry `scope.ontology_id` for direct sources; Nexus opens that ontology/item/page without guessing an association. Model answers and rapport rules are unchanged.

## Verified rich responses

`companion_rich_response_enabled` defaults to true for new or missing settings
and applies only to `evidence_v2`. Explicitly saved false values and pipeline/model
settings are preserved. Deploy matching API, workers, ShreckWorld and clients
together; existing installations with a saved false value can enable it through
Administration or `/config/`.
The existing six model roles remain sufficient. Character Companion can return
ordered narrative, entity/scene cards, images/galleries, one structured property
table and exact book excerpt cards. Each factual addition is verified with the
private narrative before the existing atomic final-message/event/rapport commit.
Unverified blocks are omitted; optional media failure preserves verified prose.
Commentary, ontology switching, grants and factual coverage retain v2 behavior.

Composition is deterministic. It selects sources cited by the draft, projects
Atlas/Foundry scalar properties whose labels occur in that source's narrative,
and uses exact supporting book quotations. Entity descriptions reuse bounded
factual draft segments rather than generating a second account. At most eight
blocks, four image assets, one twenty-row/eight-column table and 64 KiB of rich
JSON are admitted. No rich blocks are generated for casual chat or clarification.
The optional media budget is `companion_rich_response_timeout_s` (default two
seconds, range 0.1–10), shared by discovery and final Atlas image checks and
bounded by the remaining turn deadline. The existing batched evidence call
verifies block claims with narrative; there is no extra model role/search loop.

The job owner is `app/jobs/companion/rich_response.py`; the typed public contract
is `app/schemas/companion_rich_response.py`. Source reference normalization,
Atlas property projection and media adapters live in `app/integrations/companion/`.
`CompanionRepository.message_records` projects the saved envelope into private
history. The envelope is stored in existing turn result JSON; no additional
Companion database migration or historical backfill is needed. The separate v2
commentary migration remains required. Text fallback includes unique block facts
and is used by old clients, conversation context and rapport.

Atlas artwork is the admitted entity/scene's own `node_avatar_url`, mapped only
inside configured local media storage. No category/ontology fallback is claimed
to depict a specific entity. Source scope, file hash and revision are rechecked
before commitment and protected delivery. Milestones have no standalone rich
card; their existing narrative evidence remains available. Atlas tables use the
retrieved property snapshot and authoritative scoped `OntologyProperty` labels.

ShreckWorld supplies source-hash/document/page-bound thumbnails and PDF picture
crops through its authenticated media endpoints and optional media-only backfill.
Media extraction changes no embeddings or active document metadata. Unsupported
source formats keep text excerpts. Foundry context version 2 can indicate that a
document has artwork without transmitting a file URL; Foundry alone resolves it
against the original table, current document revision and read permission.
ShreckOS shows a local-Foundry-image explanation instead of fetching that file.

Web images require `companion_web_images_enabled=true`, the configured JSON web
provider, rich responses enabled, and an available web provider. This new
provider opt-in defaults false; model-managed search does not claim image support.
The JSON provider may return `images` on each factual result with `url`,
`source_page_url` equal to that result's URL, `relation:"page_image"`, and nonblank
`attribution`. Public-address checks apply. Related search hits without that
association are discarded. Web pictures are labeled source images, never proof
of the depicted subject; both clients require an explicit load click and send
no service bearer token with that external request. There is no image proxy or
arbitrary server-side remote image fetcher.

See the [rich response implementation and acceptance record](Rich%20Response%20Plan.md)
and [HTTP/history contract](Frontend%20API.md#rich-response-v1). Disable composition
to stop generating new rich envelopes while preserving saved replies. Production
provider quality/latency, real Foundry sheets and human image-grounding acceptance
remain rollout gates; do not infer them from fixture-based tests.

## Compact evidence assessment and extractive recovery

`jobs/companion/evidence_context.py` owns compact evidence projection and the
backend extractive renderer. Assessment, synthesis and verification receive an
evidence ID, source type/title/page, exact excerpt, essential structured fields,
intended need IDs, epistemic category, minimal scope/source identity and coverage
state. They do not repeatedly receive navigation URLs, geometry, hashes, embedding
vectors, document blobs or full nested retrieval plans. Rich verification retains
visible block claims and compact provenance while delivery/navigation stays
server-side. Full evidence references remain in the turn/Answer Breakdown and
are used for final access/revision checks.

The backend admits at most five unique records per retrieval tool across all
its calls, allocating across information needs before filling remaining capacity.
Atlas, ShreckWorld and memory contribute at most fifteen records; Web contributes
five more. Chronicle retains a separate five-record cap. Bounded Foundry snapshots
and image evidence are additional inputs, not silently discarded to fit a universal
twenty-record limit. Stable within-source ranks, query overlap and source diversity
make selection independent of completion speed.

Assessment receives at most eight whole compact excerpts, also bounded by
`companion_max_evidence_tokens` (default 10,000). Synthesis receives a separately
budgeted projection of the retained combined pool; final verification receives the
same selected passages. Projection omissions never erase admitted source records
or navigation metadata. `diagnostics.evidence_omissions` records collection,
assessment, synthesis and extractive omissions. Whole records are omitted from
model context if oversized; other candidates may still fit.

Primary assessment allows `companion_evidence_timeout_s` (default 600 seconds).
There is at most one additional 60-second retry, clipped to the configured timeout
and remaining turn time. `ASSESS_RECOVERY` uses the same `EvidenceAssessment`
contract and identical input, including the Web availability decision. Persistent
failure sets `assessment_unavailable`; grounded synthesis and claim verification
still run if time permits, with explicit limitations. Extraction is the final
fallback if supported synthesis cannot be delivered. Default total turn limits
and saved timeout migrations are unchanged.

New turn results use `diagnostics.assessment_contract_version="sufficiency_v1"` and
`result.assessment` is the two-field decision or null. Historical raw per-need
assessment objects remain unchanged/readable. There is no fabricated compatibility
mapping from a Boolean to legacy support statuses. No SQL migration, graph rewrite
or re-embedding is required. Drain active turns and deploy/restart API and workers
with matching prompts/contracts; roll back the bundle without rewriting history.

Extractive responses have `outcome="limited"`, `coverage="partial"`, an explicit
uncertainty segment and backend-only `extractive` segments. These segments contain
quoted source text or directly serialized essential source fields, attribution
and citations; `claims=[]`. They do not assert that the source claims are true,
relevant, exhaustive, applicable to the chosen edition, or free of conflicts.
At most four passages are rendered within the answer token budget (capped at
2,000 for extraction). A quote may use a contiguous prefix of at most 1,600
characters, explicitly labelled when shortened; its full source passage remains
in evidence. Literal source markers/Markdown/HTML are escaped. Empty evidence
produces limitations only, never invented citations or model-knowledge fallback.
Source revocation, revision changes, cancellation and lease loss remain fail-closed.

`diagnostics.evidence_assessment` and `metrics.evidence_assessment` preserve every
attempt: stage, UTC start/end, elapsed milliseconds, input record/source counts,
compact prompt characters/UTF-8 bytes/estimated tokens, model target, configured
and effective deadlines, structured-response status, exception class and safe
validation rule paths. They record same-context retry/grounded-synthesis/extractive mode,
retained evidence and unique-source counts, and counts used for synthesis or
extraction. Provider exception text and raw model output are not copied into these
diagnostics. Existing stage metrics/events remain available; saved final-message
`answer_details.diagnostics` exposes the same owner-only record. The existing
Answer Breakdown continues to show passages, citations, counts and warnings.

ShreckWorld calls add `source_readiness` (`ready`/`partial`) and
`sources_requiring_attention` (`library_item_id`, `title`, `reason`). Reasons are
`index_not_ready`, `incompatible_index` or `empty_index`. Counts are scoped to the
requested books and are disjoint: searchable active generations count as searched,
even if a newer indexing attempt is queued/failed; incompatible existing indexes
count as requiring reindexing; remaining unsearchable books count as not ready.
Deploy the updated ShreckWorld publisher to receive source IDs; older responses
retain their accurate count diagnostics without invented source identities.
No SQL schema migration or re-embedding is required for this response change.
