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
Tools are `{"allowed":["atlas","character_memory"],"web":"disabled"}`;
`allowed:null` permits configured sources. Web is `disabled`, `allow` or `require`.
Unavailable required tools fail admission. Retry uses a new idempotency key and
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

Segments distinguish conversation, fact, subjective_memory, opinion, speculation
and uncertainty. Citation IDs must refer to supplied authorized evidence. Memory
citations are subjective. User message manifests include private `user_attachment` references with an
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

Model targets include `web_search_enabled`, default `false`. Enabling it on the
synthesis target permits OpenRouter retrieval without a separate JSON search URL;
`tools.web` still gates each turn. Provider source excerpts enter the existing
evidence/source events and final citation validation. See
[shared configuration](../../Architecture/LLM_WEB_SEARCH.md) for capability
discovery, unsupported models, budgets, and rollout.


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
payloads remain valid. The speaking CharacterAgent, selection version, owner-only
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
