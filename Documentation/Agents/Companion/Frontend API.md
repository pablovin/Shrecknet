# Character Companion HTTP and streaming API

The implemented namespace is `/users/me/character-companion`, authenticated with
the existing bearer access token. ShreckOS uses `/api/shrecknet` as its proxy.
Legacy `/users/me/companion` routes retain their contracts. All resources are private
to the current user; ownership failures return 404. Responses are `no-store`.
The generated OpenAPI schemas are authoritative for every field and validation rule.
See [feature behavior and operations](Companion.md).

## Routes

Paths below are relative to the namespace. New chat, turn and reset POSTs require
a UUID `Idempotency-Key` header. Exact replays return the saved admission response;
a changed payload with the same key returns 409.

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/capabilities` | Enablement, model configuration, image/tool capabilities and limits |
| GET | `/candidates` | `ontology_id`, `search`, `cursor`, `limit`; authorized active characters |
| GET / PUT / DELETE | `/selection` | Read, select with `{character_agent_id, expected_version}`, clear with `expected_version` query |
| GET | `/relationships` | Paginated pair rapport records |
| GET | `/relationships/{character_id}/rapport` | Six scores, version, generation, pending/failed counts |
| GET | `/relationships/{character_id}/rapport/history` | Paginated audit entries |
| POST | `/relationships/{character_id}/rapport/reset` | `{expected_version}`; generation advances |
| GET / POST | `/chats` | List (`character_agent_id`, cursor, limit) or create |
| GET / PATCH / DELETE | `/chats/{chat_id}` | Read; rename `{title, expected_revision}`; enqueue content purge (202) |
| GET | `/chats/{chat_id}/messages` | Chronological page, `before` cursor, limit |
| POST | `/chats/{chat_id}/turns` | Durable admission (202), Location result URL |
| GET | `/turns/{turn_id}` | Status, provisional draft, result/error, metrics, last event cursor |
| POST | `/turns/{turn_id}/cancel` | Idempotent cancellation; terminal results remain terminal |
| GET | `/events` | Account SSE; `after` or `Last-Event-ID` |
| GET | `/turns/{turn_id}/events` | Turn SSE; same cursor contract |
| POST | `/attachments` | Multipart `file`; private normalized image (201) |
| GET / DELETE | `/attachments/{id}` | Private metadata or deletion (202) |
| GET | `/attachments/{id}/content` | Authenticated PNG bytes |

Page responses are `{items, next_cursor}`, default limit 20, maximum 100. Cursors
are opaque and owner-bound; restart pagination when changing filters. Chat, turn,
attachment and idempotency IDs are UUIDs. Character IDs are canonical graph IDs.

## Requests and results

Create a chat with
`{"character_agent_id":"graph-id","selection_version":1,"title":"Conversation","default_scope":null}`.
The selection version must match current selection and the selected character.
Chats remain bound to their original character after selection changes.

A minimal turn is `{"text":"Hello","selection_version":1}`. Optional fields are
`attachment_ids` (up to four), `context`, `foundry_context`, `tools` and `retry_of_turn_id`.
Tools are `{"allowed":["atlas","character_memory"]}`; `allowed:null` permits
configured sources. The legacy `web` field (`disabled`, `allow`, `require`) is
accepted for backward compatibility but no longer gates retrieval: web search is
available whenever configured and permitted by `tools.allowed`; the router selects whether to use it.
Unavailable sources remain explicit retrieval outcomes. Retry uses a new idempotency key and
references the prior failed/cancelled/interrupted turn; never resubmit on reconnect.
Server startup interrupts unfinished turns with retryable error code
`server_restarted`; their chats become available for a new turn. Outstanding
rapport evaluations are settled as failed without changing scores.

Context fields are `surface` (`global`, `atlas`, `shreckworld`, `chronicle`),
`world_id`, `ontology_id`, `entity_instance_id`, `source_id`, `scene_id`,
`shreckworld_id`, `library_item_ids`, `chronicle_id`, `chronicle_revision`,
`section_id`, and `selection_text`. IDs are navigation/retrieval hints, never an
access grant. Book scope requires a ShreckWorld ID; Chronicle surface requires a
Chronicle ID. Explicit page scope is validated independently from character memory.

Admission returns `turn_id`, `chat_id`, `status:"queued"`, `user_message_id`,
`events_url` and `result_url`. States are queued, routing, retrieving, synthesizing,
validating, completed, failed, cancelled and interrupted. Snapshot includes
`draft`, `result`, `error`, `rapport_status`, `metrics` and `last_event_id`.
A completed result contains text, typed segments, citations, coverage/warnings,
model targets and provider usage when reported; cost may be null.

New answers contain one `conversation` segment for the entire response; its
`evidence_ids` identify supplied sources referenced by the model, not verified
claim-level support. Older answers may retain other segment kinds. Memory evidence
is subjective. `coverage` is `unknown` unless tool failure, partial source readiness/Foundry
context or rejected citation markers make it `partial`. User message manifests include private `user_attachment` references with an
`attachment_id` and content hash. Load their bytes only through the authenticated
attachment content route; do not construct public media URLs. Chronicle references
carry `section_id` (`note:revision:reply-or-zero:Unicode-offset`) for revision-aware
passage navigation.

Render text safely and use typed navigation references;
never render source text or model output as trusted HTML.

## Streaming and failures

Use authenticated fetch streaming, not a browser EventSource with a token in a URL.
SSE frames contain `id`, `event`, and JSON `data` with `version:1`, `event_id`,
`occurred_at`, `chat_id`, `turn_id` and `payload`. Events include turn lifecycle,
`routing.status`, `retrieval.status`, `source.discovered`, `companion.interim`,
`response.delta`, `response.final`, and rapport updates/failures. Deltas/interims
are provisional; only a completed final is a delivered assistant message.

Reconnect with the last event ID and deduplicate by event ID. Expired cursors return
410: read the snapshot, then resume the account stream. No stream reconnect should
submit a turn. Heartbeats are comments. Turn streams close after terminal events;
account streams continue for asynchronous rapport updates.

Errors use FastAPI `detail`; domain errors have `{code, ...}`. Relevant statuses:
401 invalid/expired access; 404 private/missing resource; 409 selection/revision/
idempotency conflict or chat busy; 410 expired event cursor; 422 invalid input;
429 rate/concurrency limit; 503 disabled feature, missing models or unavailable DB.
Retrieval partial failures appear as diagnostics/coverage, not fabricated citations.

## Provider web-search option

Only `model_companion_web_search` may enable provider-managed Web search.
A configured JSON search URL takes precedence. The router can formulate any query;
there is no public-query catalog or private-information filter. `tools.allowed`
restricts all retrieval, including Web; `allowed:[]` disables every tool. The legacy
`tools.web` field is accepted but ignored for tool selection. Query strings are
bounded to 500 characters, with at most two Web calls in one parallel tool round.
Provider source excerpts become evidence; generated summaries do not. Search is
disabled on routing, image interpretation, synthesis and rapport targets.
See [shared configuration](../../Architecture/LLM_WEB_SEARCH.md).

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

## Foundry context snapshots

`GET /capabilities` adds `foundry_context` with `versions: [1]`, `documents: 6`,
`bytes: 65536`, `document_characters: 8000`, `description_characters: 4000`,
`fields: 100`, and `links: 20`. Clients must check support before sending the
optional `foundry_context` on `POST /chats/{chat_id}/turns`. Existing text/image
payloads remain valid. Ordinary text-turn idempotency hashes from before this
additive field remain replayable across an upgrade. The speaking CharacterAgent, selection version, owner-only
chat authorization, cancellation, quota and durable admission contracts are unchanged.

Example additive turn input:

```json
{
  "text": "What can you tell me about this character?",
  "selection_version": 1,
  "context": {"surface": "global", "ontology_id": 7},
  "foundry_context": {
    "version": 1,
    "captured_at": "2026-11-06T12:00:00Z",
    "table": {
      "foundry_world_id": "campaign-table",
      "title": "The Night Watch",
      "description": "A city campaign with house rules.",
      "foundry_version": "14",
      "system_id": "custom-system",
      "system_title": "Our RPG",
      "system_version": "1",
      "shrecknet_world_id": "city-world",
      "shrecknet_world_name": "The City"
    },
    "documents": [{
      "uuid": "Actor.hero",
      "kind": "Actor",
      "name": "Bao",
      "captured_at": "2026-11-06T12:00:00Z",
      "source_revision": "12",
      "parent_uuid": null,
      "coverage": "partial",
      "text": "A defender of the city.",
      "fields": [{"label": "Power Points", "value": 8}],
      "links": []
    }]
  }
}
```

The strict envelope contains integer `version` (exactly 1), required timezone-aware
`captured_at`, nullable `table` (default null) and `documents` (default empty).
It is not a substitute for the existing nonblank text or image requirement.
Unknown keys, duplicate document UUIDs and normalized compact UTF-8 JSON exceeding
64 KiB are rejected with 422. Document arrays permit at most six entries.

When present, `table` requires `foundry_world_id` (1–128 characters), `title`
(1–255), `foundry_version` (at most 64), `system_id` (at most 128), `system_title`
(at most 255), and `system_version` (at most 64). Optional nullable fields are
`description` (at most 4,000), `shrecknet_world_id` (1–64) and
`shrecknet_world_name` (at most 255). Foundry and Shrecknet World IDs are distinct
strings. `PageContext.world_id` retains its legacy positive-integer contract;
never put a Shrecknet World string there. A Shrecknet World has no description
column; this description is supplied Foundry table background.

Each document also accepts optional nullable `foundry_world_id` (1–128 characters)
for source navigation when table background is omitted. It must match the table
ID when both are supplied; a mismatch returns 422. Each document requires `uuid`, `kind` (Actor, Item, JournalEntryPage), `name`
(1–255) and timezone-aware `captured_at`. `source_revision` is nullable, at most
128 characters. `parent_uuid` is nullable. UUID strings are 3–512 characters,
dot-separated nonempty components containing letters, digits, `_` or `-`.
`coverage` is complete/partial/unknown (default partial); `text` defaults empty,
at most 8,000 characters. `fields` defaults empty, at most 100 objects containing
`label` (1–80 characters) and required scalar `value` (string at most 500, finite
number, boolean or null). `links` defaults empty, at most 20 objects containing
`uuid` under the same identifier rules and `label` (1–255 characters). Objects do
not accept arbitrary system JSON. Timestamps/revisions/permission claims remain
client claims, not proof of freshness or visibility.

Admission validates the referenced Shrecknet World and replaces its supplied name
with the current canonical name. `context.ontology_id`, when supplied, must belong
to that World. A sole ontology is selected automatically; otherwise no Atlas
ontology is chosen until the caller selects one. There is no fallback to the
speaking character's unrelated ontology. `character_ontology_id` still refers to
that character for subjective memory. Without a table World, explicit ontology or
Chronicle context is rejected. A missing World returns `422 foundry_world_unavailable`;
a foreign ontology returns `422 context_scope_mismatch`; detached explicit scope
returns `422 foundry_world_required`. Foundry turns do not implicitly route
Chronicle searches; callers must supply an explicit `context.chronicle_id`.
Rules retrieval uses an explicit `context.shreckworld_id` and existing authorized
service policies, never a guessed rules edition. Setup-key bindings and browser
role claims do not grant content access.

Snapshots become evidence with `source_type: "foundry"` and
`epistemic_category: "user_provided"`. User message manifests retain admitted
snapshots; assistant manifests contain cited snapshot evidence. Table source
excerpts contain the typed table JSON. Document evidence carries readable `excerpt`,
`structured_evidence: {fields, links}`, and `reference` with document UUID/kind,
capture time/revision, parent UUID and nullable `foundry_world_id`. Generic Foundry
sources are partial; answer coverage cannot be promoted to complete when such
partial evidence is present. Source controls should resolve only admitted/cited
references locally and recheck current table and permission before opening.
The backend has no live Foundry connection and cannot validate a player's browser
permissions. Never treat descriptions or document text as agent instructions.

All snapshot sources count against the configured evidence-token budget. Excess
returns `413 foundry_context_budget_exceeded`; combined image/snapshot overflow is
also fenced before synthesis. No snapshot records are silently dropped to fit.
Retain the exact original body and idempotency key across ambiguous retries;
refresh extraction only for a new turn. Foundry snapshot turns do not reuse prior
retrieval evidence cached for a different game situation. Snapshots are stored
in existing turn request JSON and message manifests, removed by normal chat purge;
no schema migration or writes to Foundry documents occur. Removing a context chip
affects future turns, not historical conversation content.

## Answer tool and evidence breakdown

Completed turn snapshots (`GET /users/me/character-companion/turns/{turn_id}`)
and `response.final` retain the existing result fields and add `result.tool_calls`.
Each entry contains `tool` (`atlas`, `character_memory`, `shreckworld`, `chronicle`,
`web`), `status` (`ok`, `empty`, `timed_out`, `unavailable`), `query` (the
tool-specific retrieval query requested this turn), `cache_hit`, nullable `elapsed_ms`, `returned_count`,
and `evidence_ids`. Cached retrieval uses the current request query; it does not
claim to have run another search. Missing timing is null, never an invented zero.
`returned_count` counts the bounded adapter results, not the provider's total hits.
`evidence_ids` includes only results admitted to the turn evidence budget;
excluded payloads are not published. Empty/failed tools remain visible.

ShreckWorld tool calls retain `source_coverage` with `requested_source_count`,
`searched_source_count`, `sources_without_ready_index` and `sources_requiring_reindex`.
Readiness is independent of retrieval success: passages return `status="ok"`,
no passages return `status="empty"`, and service failures retain `unavailable` or
`timed_out`. `source_readiness="partial"` and optional `reason` of
`incompatible_index`/`index_not_ready` identify incomplete index coverage without
invalidating returned passages. `sources_requiring_attention` lists scoped
`{library_item_id,title,reason}` entries; reason is `index_not_ready`,
`incompatible_index` or `empty_index`. An older service may supply counts without
individual source IDs. A malformed response without required coverage remains an
unavailable retrieval response. Reindex affected sources through ShreckWorld.

`result.evidence` contains all supplied evidence, including uncited records.
For new answers, the single segment's `evidence_ids` maps the entire answer to
sources selected by the model using optional inline markers. The backend strips
markers from displayed text and ignores unknown markers with a warning; missing
markers yield no citations, not a failed answer. The existing `citations` field
keeps its shape. Evidence includes excerpts, structured evidence,
source references/revisions and navigation. Attached images and Foundry context
are supplied context, not retrieval tool executions. Source access/revisions for
all supplied evidence are checked before final commit. Citation links describe
model-declared answer-level support and do not prove semantic grounding or identify
which particular statement a source supports.

`GET /users/me/character-companion/chats/{chat_id}/messages` adds nullable
`answer_details` to each message: `{tool_calls, evidence, segments, coverage,
warnings}` from the completed turn. It is null for user messages and replies
created before tool tracing was available. This is owner-only historical content,
subject to the existing chat authorization and deletion rules. It is a snapshot,
not a guarantee that the original source remains accessible. Raw provider payloads,
credentials, internal exceptions and model reasoning are not included.

The contract is additive and uses existing turn JSON storage. Deploy API and turn
workers before the frontend. No schema migration or historical backfill is needed;
older clients keep using `manifest` and older replies keep their source cards.


## Evidence v2

Existing endpoints and coarse `TurnRead.status` values are preserved. The Markdown
pipeline is the sole execution path; retired pipeline selectors do not select a
legacy runner. User capabilities retain general-knowledge opt-in discovery and
model role configuration. Evidence/interim roles remain compatibility settings.

`POST /users/me/character-companion/chats/{chat_id}/turns` accepts additive fields:

```json
{
  "text": "Use the other world and explain the rule",
  "selection_version": 1,
  "context": {"surface": "global"},
  "tools": {"allowed": null},
  "general_knowledge_opt_in": false,
  "use_page_scope": false,
  "clarification_id": null
}
```

`clarification_id`, when supplied, must match the chat's pending UUID or admission
returns `409 stale_clarification`. `use_page_scope=true` releases a conversational
scope override and uses validated submitted/default page scope. Optional fields
at their defaults do not change pre-upgrade idempotency hashes. V2 stores the
original request separately from server-owned `execution_context`; clients cannot
submit that validated execution field or choose retrieval grants.

Chat responses add `resolved_ontology` (ID, name, description, RPG system, resolution source and revision),
`scope_override`, and nullable `pending_clarification` with ID, original query,
questions, kind and target ID. Clarification finishes a turn with result
`outcome="needs_clarification"` and frees the chat lock. A relevant next message
resumes its pending question; a new topic abandons it.

Message responses add `kind` (`user`, `commentary`, `final`) and `sequence`.
Assistant sequence zero is the final; positive values are durable commentary.
Use creation time/ID transcript order, not sequence alone. Existing message IDs,
roles, content, manifests and owner-only pagination remain. Commentary has no
answer breakdown and is excluded from rapport and completed-exchange context.

Turn snapshots add `stage`, validated `execution_context` and `commentary` records.
SSE keeps version 1 envelopes and adds:

- `pipeline.status`: `stage` plus optional `tool`/`status`, for resolution,
  planning, scope updates, retrieval, synthesis, media resolution and final
  source/citation validation. These are short progress labels, never model reasoning.
- `message.committed`: a persisted commentary record including stable message ID.
  Refresh owner-scoped message queries; deduplicate repeated events by message ID.
- `ontology.updated`: `previous` and `ontology` metadata after a validated switch.
  Refresh chat scope and display its active name.

Commentary survives event retention and failed/cancelled turns. At most one static
acknowledgement is generated; positive `companion_interim_max_messages` enables it.
The pipeline buffers Markdown privately until required access checks; no failed
truncation attempt is emitted as `response.delta`. `response.final` is authoritative.
New results have `diagnostics.version="markdown_v3"`, one `conversation` segment,
`assessment:null` and `rejected_claims:[]` for historical compatibility. Stored older
assessment/segment payloads remain readable. Citation links are answer-level
attribution and do not prove semantic correctness. Coverage is retrieval diagnostics,
not a sufficiency certificate. General knowledge requires policy plus turn opt-in.

No additional schema migration is required. Drain turns and deploy API/workers with
matching prompts/contracts. Rollback must retain historical result readers and all
new conversation/rapport writes.

## Rich response v1

`GET /users/me/character-companion/capabilities` adds
`rich_response: {versions:[1], block_types:["narrative","image","gallery",
"entity_card","table","book_excerpt"], enabled:boolean}`. `enabled` requires both
`companion_rich_response_enabled` in the sole runtime pipeline. Capability absence means
ordinary text support. Existing admission payloads and idempotency keys remain
unchanged; presentation is server configuration, not a client access grant.

Completed turn `result`, `response.final` and final-message `answer_details` add
nullable `rich_response`. The version-one envelope contains `version`, ordered
`blocks`, `assets` and typed `targets`. User/commentary messages have no envelope.
The existing `text`, `content`, `segments`, `evidence`, `citations`, coverage and
warnings remain valid. Saved text is complete readable Markdown; optional cards
do not append a duplicate narrative or long excerpt appendix. Unknown versions
and malformed presentation fall back to text. Output stays private until required
source/access checks pass; no model verifier runs.
`pipeline.status` includes `resolving_media`; coarse turn
statuses and version-one SSE envelopes do not change.

Every block has `id`, `type`, `evidence_ids`, nullable `target_id`. Narrative uses
ordered `segment_ids`, referencing the narrative segment exactly once. Other blocks
have `fallback_text` and type-specific fields:

| Type | Fields |
| --- | --- |
| `entity_card` | `entity_ref:{ontology_id,node_id,node_label}`, `name`, nullable `description`, nullable `asset_id` |
| `image` | `asset_id`, neutral `alt`, nullable `caption` |
| `gallery` | `items:[{asset_id,alt,caption,evidence_ids,target_id}]` |
| `table` | `title`, `columns:[{id,label}]`, `rows:[{id,cells:[{value,evidence_ids}],evidence_ids,target_id}]`; scalar/null values, rectangular rows |
| `book_excerpt` | exact `quote`, `title`, `document_ref`, nullable `asset_id` |

A book reference includes exactly one of `ontology_id`/`shreckworld_id`, plus
`library_item_id`, `document_id`, `source_sha256`, physical one-based `page_number`
and nullable printed `page_label`. Existing evidence retains citation rectangles
and full page provenance; merge typed target locations with that evidence when
opening the reader rather than discarding it. Entity cards use actual scoped
Atlas node IDs/labels. Foundry targets use `foundry_world_id`, `uuid` and nullable
`source_revision`; Chronicle targets use `chronicle_id`, `revision` and nullable
`section_id`; web targets use `url`. Each target has an envelope-local `id` and
`kind` (`atlas`, `shreckworld`, `chronicle`, `foundry`, `web`).

Assets contain `id`, `source_type`, `evidence_ids`, nullable `source_revision`,
`provenance_kind`, nullable `mime_type`/`width`/`height`, `attribution`, `target_id`
and `delivery_ref`. The referenced target supplies the source identity. Delivery
is `{kind:"service",service:"shrecknet"|"shreckworld",path}`, a Foundry-local
`{kind:"foundry_local",reference}`, or an explicitly loaded external
`{kind:"external",url}`. Service paths are bound to the owning trusted service;
never send bearer tokens to an arbitrary URL. IDs are references, not access grants.
At most eight blocks/four assets/twenty targets and 64 KiB of envelope JSON are
accepted; tables have at most eight columns/twenty rows and one table per answer.

`GET /users/me/character-companion/turns/{turn_id}/assets/{asset_id}` serves only
saved Atlas raster assets for the current owner and a non-deleted chat. It
rechecks character availability, ontology existence, graph source, image revision,
file hash and storage confinement. Returns 404 for unavailable/private assets,
409 `asset_changed` for replaced artwork, and private/no-store responses with
`X-Content-Type-Options:nosniff`. It never fetches an arbitrary remote image URL.
ShreckWorld assets use that service's current source authorization and revision
checks; see the [reader media API](../../API/SHRECKWORLD_READER_API.md#document-media).
No signed URLs, tokens, file paths or blob URLs are persisted in the envelope.

Clients render narrative from referenced segments and optional blocks once;
retain source details and per-cell citations. A failed image leaves its card or
excerpt readable. Preserve row/cell IDs when sorting. Unknown/malformed envelopes
fall back to the complete text; do not discard the reply. Rich response history
and final events must agree and survive event expiry, polling/reload and pagination.
Expand reveals admitted content only; obtaining new facts requires another
normal authorized request. No HTML/component code/macros are accepted as blocks.

Foundry context capabilities now advertise versions `[1,2]`. Version 2 adds
`documents[].image_available` (boolean, default false); it carries no image URL or
server-certified permission. Version 1 rejects a true image flag and its
normalized request omits the new false default, preserving old idempotency hashes.
Negotiate version 2 before sending image availability. Other limits, document
kinds, scope rules and immutable-retry guarantees remain unchanged.

## Retrieval execution and deadlines

The router selects queries and sources, including memory and Web; there is no
separate memory relevance or evidence sufficiency call. Atlas/ShreckWorld retain
light/deep internal adapter contracts. Normal requests have at most three tasks,
nine executable calls and two Web calls, executed in one concurrent round.
`tools.allowed` applies to all sources. Empty results are distinct from unavailable
and timed-out sources, and do not invalidate successful siblings.

`result.tool_calls[]` retains `retrieval_depth`, `timeout_s`, `timeout_layer`,
source readiness, source attention, need IDs and coverage traces. New calls have
`recovery:false`; old recovery records remain readable. Omitted projection records
remain in `result.evidence`, with reasons in `diagnostics.evidence_omissions`.
Per-call defaults remain ten minutes and the retrieval window 615 seconds; total
saved turn limits remain unchanged. Admission still returns 202 immediately.

<a id="assessment-recovery-and-execution-diagnostics"></a>

## Markdown generation and execution diagnostics

`result.text` is readable Markdown with source markers removed. Aliases are resolved
only against the exact synthesis evidence projection. Unknown/malformed markers
produce no links, add warnings and make coverage partial. `citation_validation`
records `valid`/`unknown` marker counts and a `malformed` Boolean. These counts also
appear in metrics and owner-only `answer_details.diagnostics`; they measure ID
integrity, not factual support. The backend does not extract or verify claims.

Length termination retries once with identical evidence and shorter-answer guidance.
The final attempt replaces the private failed attempt. Persistent generation failure
sets the turn to `failed` with a retryable safe code: `synthesis_truncated`,
`synthesis_generation_failed`, `empty_synthesis`, `invalid_synthesis_response`,
`synthesis_output_too_large` or `synthesis_context_budget_exceeded`. Required source
changes/access/validation failures use `source_changed`, `source_access_denied` or
`source_validation_failed` and never
publish the draft. Optional rendering/media errors preserve cited text. Missing
usable evidence instead permits an honest completed `outcome="limited"` answer.

Metrics include model-stage duration/attempt/finish reason/usage, tools and statuses,
selected tools, source-validation time and response characters/words. No raw provider
error text is returned. Source access is revalidated before durable publication;
source links and assets retain their existing revision-aware access checks.

## Simple research planning compatibility

Routes, admission payloads, persisted scope/pending clarification and idempotency
remain unchanged. Diagnostics expose `research_plan` (`mode`, `tasks` with `query`
and `sources`, `clarification_questions`),
`planning.contract_version="simple_research_v2"`, rejected source reasons and
fallback/error/merge counts. `diagnostics.plan` is backend execution data and must
not be submitted by clients as a router contract. Technical router failure uses
bounded allowed internal search; it does not automatically call Web.

World-selection clarification text uses “world” (for example, “Which world do you
mean?”). The internal `ontology` clarification kind and ontology ID fields keep
their existing names; see [the Companion flow](Companion.md).

Final Atlas validation uses the resolved world, source label and canonical source
ID; missing, duplicate or revised records fail closed with `source_changed`.
See [Atlas source identity and final validation](Companion.md#atlas-source-identity-and-final-validation).
