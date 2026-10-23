# CharacterCompanionAPI

Private canonical-character conversations; see the Character Companion workflow guide.

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

### `send(self, chat_id, text, selection_version, *, idempotency_key, attachment_ids=None, context=None, tools=None, retry_of_turn_id=None)`

Admit one turn with a stable UUID key; this returns before synthesis.

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

Read the four administrator-only Companion model assignments.

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
