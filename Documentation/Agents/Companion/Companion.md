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

The legacy worker pipeline (`companion_query_pipeline_version="legacy"`) is:

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
referenced body/reply passage and warns when the revision has changed. Optional web search
uses a configured JSON HTTP provider; it does not fetch arbitrary result pages.
The question itself is not automatically redacted or converted to a public-only
query; users should not include private information in questions when permitting
web search. Search remains subject to the existing per-turn permission and routing.
Evidence is capped at twenty records and the configured estimated token budget.
The final context also has a bounded token estimate; model-specific context-window
fit and image token costs still require deployment validation.

ShreckWorld reports the number of sources searched, not indexed, and requiring
reindexing for the active embedding fingerprint. The Companion preserves those
counts and passes partial-search outcomes to synthesis; compatible passages can
still be used. An incompatible or unready index is not reported as an empty
search. Reindex affected sources with ShreckWorld's existing indexing workflow.
Rules evidence is not reused from the prior turn because the active fingerprint
or source availability may have changed.

## Evidence-first query pipeline

`companion_query_pipeline_version` defaults to `"evidence_v2"` in both code and
the initial configuration seed. New installations and existing configurations
missing this setting use the ontology-aware pipeline automatically after startup.
An explicitly saved `"legacy"` selection remains respected for rollback and is
editable in Administration → Shrecknet → Companion. Six independently configured model roles
cover routing, dedicated web search, evidence review, synthesis, commentary and
rapport. All inherit the repository's OpenRouter DeepSeek Flash model default;
only the dedicated web target defaults to `web_search_enabled=true`.

1. **Scope fast path.** Validate an explicit page/Foundry ontology or reuse the
   chat's saved ontology, skipping the ontology-inference call. Missing scope is
   inferred using the routing model and bounded authenticated ontology discovery.
   Unresolved scope produces a direct question by world name. The character's
   ontology is never substituted for an unresolved conversational world.
2. **LLM source planning.** The routing model declares bounded needs and separate
   queries for available Atlas, ShreckWorld, Web, Chronicle and character-memory
   sources. Clarification ends the turn and releases the chat; its pending
   question is resumed on the next relevant message. New topics abandon pending
   clarification. Malformed planning never silently becomes a factual answer.
3. **Ontology update before retrieval.** A router-requested world switch is
   validated and saved before any retrieval. It clears incompatible source/page
   bindings, updates grant scope and replans once. A conflicting explicit page
   selection requires clarification. A confirmed conversational override persists
   across subsequent page snapshots until `use_page_scope=true`. The selected
   character, character-memory scope and rapport stay unchanged. A committed
   switch survives later retrieval failure or cancellation.
4. **Independent parallel retrieval.** Atlas reuses Elder planning/execution
   without synthesis. ShreckWorld uses its planned evidence pipeline. The dedicated
   web model uses gateway search and actual provider excerpts; its generated
   summary is not evidence. JSON web search remains an explicitly selected
   alternative. Tool failures/timeouts retain successful siblings. Existing
   user/source authorization, private grants, revision checks and evidence budgets
   remain enforced; v2 does not reuse earlier-turn evidence.
5. **Assessment and recovery.** The evidence model checks coverage, contradictions,
   missing facts and exact supporting excerpts or serialized structured fields. One configurable recovery round
   can search specific unresolved needs and reassess once. Empty/failed searches
   do not establish absence. Unverifiable assessment returns a limitations reply.
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

General-knowledge fallback defaults to disabled. With administrator policy
`labeled_opt_in`, a turn must also set `general_knowledge_opt_in=true`. Such text
is labeled **General knowledge — not verified against your world or books** and
cannot represent verified canon or exact rule text. Per-turn web permission still
applies independently of model search capability.

An acknowledgement is persisted during admission. `pipeline.status` events report
resolution, planning, update/replanning, retrieval, assessment/recovery, synthesis
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
is insufficient. No graph migration or re-embedding is required. Rollback to
legacy must use this compatibility release, whose context/rapport reads exclude
commentary; older binaries assume one assistant message per turn. Keep additive
columns and delivered messages intact.

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

Set `companion_rich_response_enabled=true` after deploying the matching API,
workers, ShreckWorld and clients. This setting defaults to false and applies
only to `evidence_v2`; explicitly saved pipeline/model settings are preserved.
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
provider, rich responses enabled, and normal per-turn web permission. This new
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
