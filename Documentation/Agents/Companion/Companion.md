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

<a id="evidence-first-query-pipeline"></a>

## Execution and ownership

`character_companion` routes map HTTP to `CharacterCompanionService` and
`CompanionRepository`. SQL stores selections, pairs, chats, messages, turns, events,
private attachment metadata, audit records, idempotency records and a durable outbox.
Neo4j remains the authority for CharacterAgent identity and memory.

The sole runtime path is:

`User → Ontology resolution → LLM router → Parallel tools → Markdown synthesis → Deterministic rich rendering/access validation → Durable response → Asynchronous rapport`

1. Revalidate the originating user/session and canonical identity; load effective
   traits, pair rapport and bounded completed conversation history.
2. Reuse an explicit/saved validated ontology. `SCOPE_INTENT` detects switches and
   pending continuation; missing scope uses `ONTOLOGY`. Resolve page conflicts and
   persist authorized scope updates before retrieval. Character identity never
   substitutes for an unresolved conversational world. World-selection
   clarifications use “world” rather than the technical term “ontology”, including
   fallback questions when the model is unavailable. Available world names remain
   visible as choices. Internal ontology identifiers and clarification kinds retain
   their existing API contracts.
3. `PLANNER` returns `mode` (`search`, `chat`, `clarify`), `tasks` and
   `clarification_questions`. Each task has `query` (1–500 characters) and one to
   five `sources`: `atlas`, `shreckworld`, `chronicle`, `character_memory`, `web`.
   Search requires one to three tasks and no questions; chat requires both lists
   empty; clarify requires one to three questions and no tasks. The backend merges
   duplicate query/source pairs, assigns need/call IDs and admits at most nine
   calls, including at most two Web calls. The router alone decides memory relevance.
4. Intersect tool selections with `tools.allowed`, configured availability and
   validated scope. `allowed:[]` executes no retrieval; null permits configured
   sources. The legacy `tools.web` value remains accepted but does not gate Web.
   Web is an ordinary router-selected tool and accepts any query, with no public
   query catalog, privacy filter or separate semantic validation call. Prefer
   internal indexed facts/rules and personal memory when appropriate; Web is not
   automatic for every question. Technical router failure searches permitted
   Atlas/ShreckWorld using the original resolved goal, without inventing a
   clarification or automatically sending that goal to Web.
5. Optional image interpretation supplies bounded visible observations and search
   terms to selected tools, including Web. Interpretation failure preserves the
   attached images for multimodal synthesis. Selected adapters run concurrently with independent sessions/deadlines;
   failed, unavailable and empty sources do not cancel successful siblings.
   Atlas reuses Elder retrieval without Elder synthesis; ordinary light queries
   need no second planner. ShreckWorld uses single-question evidence retrieval;
   internal deep/supplied-plan adapter contracts remain supported. Chronicle needs
   explicit scope. Character memory is selected-character subjective perspective.
   Web uses actual provider excerpts, never its generated summary as evidence.
6. Aggregate evidence and select whole compact records across needs and sources.
   Synthesis receives at most twenty records within the configured token budget,
   identity/psychology/traits, rapport, question/history and retrieval outcomes.
   Assign numeric citation aliases to this exact selected order and freeze the map.
   Full admitted evidence and provenance remain retained for diagnostics/navigation.
7. `SYNTHESIZE` returns plain Markdown with `[[source:N]]` references. It answers
   supported parts directly, acknowledges missing/contradictory information and
   distinguishes campaign canon, published editions, external sources and subjective
   memories. There is no evidence assessment, sufficiency vote, claim extraction,
   structured draft, semantic verifier or repair loop. Unknown/malformed citations
   are rejected as links and reported; identifier validity does not prove truth.
   Default length guidance is 50–150 words for simple facts, 150–300 for normal
   explanations, longer for mechanics or requested exhaustive detail.
8. Detect length termination from provider metadata and retry once with
   `SYNTHESIZE_SHORT`, identical evidence/aliases and a shorter-answer instruction.
   A failed second attempt is a typed generation error, never missing evidence.
   No private generation attempt is streamed or concatenated with another attempt.
9. Resolve citations and compose optional rich blocks deterministically from cited
   metadata; media/rendering failure preserves cited Markdown. Revalidate identity,
   session, all admitted source access/revisions and attachment availability before
   committing the final exchange/event and rapport outbox atomically under its lease.
10. Evaluate rapport asynchronously in completion order; operational clarification
    and scope-only turns settle without score changes.

Prompt definitions and self-contained contracts live beside the job in
`query_prompts.py` and `prompts.py`, in execution order. Scope/routing/image/rapport
outputs remain structured; synthesis is Markdown. No competing runtime selector
exists. Historical results retain their original segments and assessment data.
New answers have one `conversation` segment and `result.assessment=null`.

`coverage` describes retrieval/context limitations, never semantic verification.
It is normally `unknown`, or `partial` for failed tools, partial source readiness,
partial Foundry context or rejected citation markers. Empty results from one tool
and context omissions never force all useful evidence to be refused. With no usable
model evidence, a research answer is `outcome="limited"`; the model describes what
is missing without inventing rules. General knowledge remains disabled unless both
administrator `labeled_opt_in` policy and per-turn opt-in permit explicitly labeled
material. Identity and social conversation do not require retrieval.

## Pipeline diagnostics and configuration

`pipeline.status` events report ontology resolution, planning, scope updates,
retrieval, synthesis, optional media resolution and final validation. At most one
static acknowledgement is persisted; positive `companion_interim_max_messages`
values enable it and zero disables it. No generated interim model runs.

`diagnostics.version="markdown_v3"` identifies new results. Diagnostics preserve
compiled plans, rejected sources, scope resolution, context omissions, truncation
counts and citation validity. Metrics record selected tools, individual tool
statuses/times, model-stage durations/usage, validation time and response length.
Safe terminal codes distinguish generation failures/truncation/empty output,
context overflow and source changes/validation failure. Provider error contents,
private source text and model drafts are excluded from ordinary error logs.

The four active model roles are routing, synthesis, Web search and asynchronous
rapport. Evidence-review and interim model keys and old recovery/interim tuning
remain accepted for configuration/SDK compatibility but are inactive. Per-call
limits remain 600 seconds, one parallel retrieval window defaults to 615 seconds,
and saved total turn limits remain unchanged. New/missing output-token ceilings
are 8,000; saved custom ceilings are preserved. Prompt guidance determines normal
length. See [model configuration](./Model%20Configuration.md).

No SQL migration, graph rewrite or re-embedding is introduced by this simplification.
Drain active turns and deploy matching API/workers/prompts/contracts together.
Existing startup migrations and historical result readers remain required. Roll
back compatible binaries without deleting new history, rapport or additive fields.

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
`shrecknet_companion_rapport_worker`. Configure routing, synthesis and
rapport models before enabling both `enable_ai_agents` and `companion_enabled`.
Evidence/interim roles are retained only for configuration compatibility. See
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
human evaluation. Each turn retrieves fresh evidence; earlier-turn evidence is not
reused because source revisions/readiness may have changed. Historical conversation text is the owner's transcript,
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
backend-to-Foundry live lookup occurs. Synthesis receives those sources alongside
ordinary retrieval evidence. Foundry facts remain client-supplied observations,
not verified rules or authority to change the speaking identity. Existing citation
validation applies, and partial snapshot coverage keeps the answer partial.
Asynchronous rapport continues to evaluate only delivered conversation quotations; it does not
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

<a id="verified-rich-responses"></a>

## Deterministic rich responses

`companion_rich_response_enabled` defaults true; saved false values are preserved.
The version-1 envelope keeps narrative, entity cards, images/galleries, property
tables and optional book excerpts. Markdown text remains the complete readable
answer when composition is disabled or fails. New composition emits at most five
blocks and four assets; the public schema still accepts historical eight-block
responses. Only cited sources contribute optional blocks. No model call verifies
or manufactures presentation.

Atlas cards use source names and available short source descriptions, never copied
model prose. Property tables use actual scalar/null source fields mentioned in the
answer, at most twenty rows and 4,000 fallback characters. Markdown comparison
and mechanics tables remain in the narrative. Book/page navigation remains available
without adding excerpts; excerpt cards require a quote/passage request and project
at most 600 source characters. No long excerpt appendix or duplicate narrative is
added. Source cards, URLs and IDs are never invented.

Atlas media uses only the admitted entity/scene's artwork, confined to configured
local storage with hash/revision checks. Book media remains document/hash/page-bound
through ShreckWorld. Foundry media is resolved only in its original table with
current document permission/revision checks. Web images still require
`companion_web_images_enabled`, the configured JSON provider's source-associated
image metadata and valid attribution. Provider-managed Web search supplies factual
excerpts only. External image loads remain explicit and receive no service token.
Missing assets prune images/galleries independently of valid cards/text.

Ownership remains `jobs/companion/rich_response.py`, with reference/media adapters
under `integrations/companion/` and the existing public response schema. History,
final event and turn snapshots preserve the same envelope. Foundry renders a safe
Markdown subset; raw HTML and arbitrary model image URLs are never executed.
See [HTTP/history contract](Frontend%20API.md#rich-response-v1).

<a id="compact-evidence-assessment-and-extractive-recovery"></a>

## Compact evidence and citation resolution

`evidence_context.py` projects exact excerpts, source type/title/page, essential
structured fields, need associations, minimal source/scope identity, epistemic
category and coverage/relevance metadata. Navigation URLs, geometry, source hashes,
document blobs and embeddings stay outside model evidence. Full retained references
remain available for access checks and Answer Breakdown.

Each tool retains at most five unique records across its calls. Need-aware
selection is independent of tool completion order. Synthesis then selects at most
twenty whole records within `companion_max_evidence_tokens`; oversized records may
be omitted while smaller relevant records remain. `diagnostics.evidence_omissions`
records collection and synthesis omissions without erasing the retained pool.

The numeric aliases resolve exclusively against the exact synthesis projection,
not the larger retained list. Unknown complete markers and malformed marker tails
are removed; warnings and `citation_validation` record valid/unknown counts and a
malformed flag. These checks establish reference integrity only. Grounding,
character fidelity and applicability require curated regression/model evaluation.

The [simplification design](./Conversational%20Simplification%20Plan.md) records
milestones and compatibility decisions. [Validation](./Validation.md) distinguishes
local provider-double regressions from live deployment acceptance.

### Retrieval worker event-loop lifecycle

Atlas uses Elder's shared query embedding runtime. Successive Companion Celery
tasks recreate loop-bound embedding queues and workers while retaining completed
query cache entries. A cancelled turn cannot leave a queue bound to its closed loop
for the next turn. See the [shared embedding runtime](../../SceneCentricMemory/Embedding/SCENE_EMBEDDING.md#shared-embedding-runtime)
for deployment and recovery of previously stalled “Checking Sources” turns.

### Atlas source identity and final validation

After synthesis, Atlas evidence is rechecked within the resolved world using its
`node_label` and own canonical ID: `EntityInstance.entity_instance_id` (with `id`
fallback), `Scene.id`, or `Milestone.id`. A milestone's `scene_id` identifies its
parent and never identifies the milestone itself. Optional source images use the
same typed lookup. Entity revisions use `last_updated_date`, falling back to
`updated_at`; scene and milestone revisions use `updated_at`. Retrieval snapshots
and final checks use the same revision rule. Missing, duplicate, or changed sources
fail closed; validation does not select an arbitrary matching record.

Failure logs include the safe `error_code` alongside the turn ID, stage and
exception type. Rebuild and recreate Companion workers to apply these rules; no
schema migration or reindexing is needed. Previously failed turns must be resubmitted.
