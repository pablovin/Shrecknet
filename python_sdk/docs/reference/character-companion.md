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
