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
`attachment_ids` (up to four), `context`, `tools` and `retry_of_turn_id`.
Tools are `{"allowed":["atlas","character_memory"],"web":"disabled"}`;
`allowed:null` permits configured sources. Web is `disabled`, `allow` or `require`.
Unavailable required tools fail admission. Retry uses a new idempotency key and
references the prior failed/cancelled/interrupted turn; never resubmit on reconnect.

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
