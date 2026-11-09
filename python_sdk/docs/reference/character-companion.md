# CharacterCompanionAPI

Private canonical-character conversations; see the Character Companion workflow guide.

Turn results remain raw dictionaries preserving tool depth/deadlines, source
readiness/attention, complete evidence and owner-only diagnostics. New replies use
Markdown, one `conversation` segment, `assessment:null` and
`diagnostics.version="markdown_v3"`. Citation validation counts, truncation and
optional media outcomes are additive. Historical assessment/segment payloads remain
readable. The router selects parallel sources including Web; any query may use Web
when allowed. There is no assessment, memory vote or claim-verifier model call.
See the [workflow guide](../character_companion.md#markdown-generation-and-failures).

## Methods

### `capabilities(self)`

No description.

### `candidates(self, **filters)`

No description.

### `selection(self)`

Read the globally persisted selection and optimistic version.

### `select(self, character_agent_id, expected_version)`

Select a canonical character using the current selection version.

### `clear(self, expected_version)`

Clear selection while preserving conversations and rapport.

### `relationships(self, **filters)`

No description.

### `rapport(self, character_id)`

No description.

### `rapport_history(self, character_id, **filters)`

No description.

### `reset_rapport(self, character_id, expected_version, *, idempotency_key)`

Reset this pair using its current version and a stable UUID key.

### `chats(self, **filters)`

No description.

### `create_chat(self, character_agent_id, selection_version, *, idempotency_key, title=None, default_scope=None)`

Create a character-bound chat; retain the UUID key for retries.

### `chat(self, chat_id)`

No description.

### `rename_chat(self, chat_id, title, expected_revision)`

No description.

### `delete_chat(self, chat_id)`

No description.

### `messages(self, chat_id, **filters)`

No description.

### `send(self, chat_id, text, selection_version, *, idempotency_key, attachment_ids=None, context=None, tools=None, retry_of_turn_id=None, foundry_context=None)`

Admit one turn with a stable UUID key; optional foundry_context is an immutable client snapshot.

### `turn(self, turn_id)`

Read a durable snapshot, including provisional draft and terminal result.

### `cancel(self, turn_id)`

Cancel a nonterminal turn and fence late provider output.

### `upload(self, filename, content, mime_type)`

Upload a private image for one future turn.

### `attachment(self, attachment_id)`

Read owner-only attachment metadata; no public storage path is returned.

### `attachment_content(self, attachment_id)`

Download authenticated normalized image bytes without JSON decoding.

### `delete_attachment(self, attachment_id)`

No description.

### `get_model_config(self)`

Read the administrator-only Companion model assignments.

### `update_model_config(self, changes)`

Update only supplied administrator model assignments.

### `events(self, turn_id=None, *, after=None)`

Stream versioned events; resume with after without submitting another turn.


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

Answer origins: `turn()` preserves `result.tool_calls`; `messages()` preserves
nullable `answer_details` with tool calls, supplied evidence and answer segments.
New answers use one whole-answer `conversation` segment; its citations are not
claim-level semantic verification.
ShreckWorld tool calls can additionally include `source_coverage` and a reason
of `incompatible_index` or `index_not_ready`.
See [the workflow guide](../character_companion.md#inspect-answer-origins) for
matching evidence IDs, cache semantics and compatibility.


Model configuration retains `model_companion_web_search` and
`model_companion_evidence`. `send()` accepts optional
`general_knowledge_opt_in=False`, `use_page_scope=False` and
`clarification_id=None`; default-valued fields are omitted from requests.
`chat()`, `messages()` and `turn()` retain additive scope, commentary and stage
metadata. Event names/payloads remain unmodified. See the
[Companion guide](../character_companion.md#evidence-first-pipeline).

### Rich presentation

- `CompanionTurn.result`: nullable raw saved result, including complete text.
- `CompanionTurn.rich_response`: nullable typed version-one `RichResponse`; absent,
  malformed and future envelopes retain text fallback.
- `CompanionRichResponse`: generated block, target and delivery unions, exported
  from `shrecknet_client`; generated definitions live in `companion_rich_response.py`.
- `presentation_config()` / `update_presentation_config(CompanionPresentationConfig)`:
  existing administrator `/config/` controls; updates send explicitly set fields.
- `rich_asset_content(turn_id, asset_id)`: authenticated saved Atlas image bytes;
  validates identifier syntax and propagates HTTP errors. Other services own their
  media contracts. Never send bearer tokens to external image URLs.

No existing turn, message or event method loses additive fields. Source access and
revisions may change after a reply is saved. See the
[workflow guide](../character_companion.md#rich-responses) for rollout and examples.


Simple research planning keeps the SDK request/response models unchanged; optional
`research_plan` and `planning` diagnostics describe semantic tasks and compilation.
See [Companion usage](../character_companion.md) for safe optional-field reads.
