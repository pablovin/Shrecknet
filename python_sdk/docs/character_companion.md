# Character Companion SDK

Use `sdk.character_companion` with the existing authenticated async client.
This resource is independent from legacy persona methods.

```python
from uuid import uuid4

api = sdk.character_companion
selection = await api.selection()
selection = await api.select("canonical-character-id", selection.version)
chat = await api.create_chat(selection.character_agent_id, selection.version,
                             idempotency_key=str(uuid4()))
turn = await api.send(chat["id"], "Hello", selection.version,
                      idempotency_key=str(uuid4()))
async for name, event in api.events(turn.turn_id):
    if name == "response.delta":
        print(event.payload["delta"], end="", flush=True)
snapshot = await api.turn(turn.turn_id)
```

Keep an action's UUID key across network retries. A changed payload requires a new
key. `events(after=last_event_id)` resumes without resubmitting; on 410 read the
turn snapshot. Deltas are provisional until completion. Account `events()` also
receives later rapport updates.

Methods cover capabilities, candidates, selection/select/clear, relationships,
rapport/history/reset, chats/create/read/rename/delete/messages, send/turn/cancel,
upload/attachment/attachment_content/delete_attachment and administrator model
configuration read/update. `attachment_content(id)` returns authenticated PNG bytes
for a private image reference in a message manifest.
Selection, rapport, turn and event models are typed Pydantic models. List/chat/image
responses retain the server dictionaries. `CompanionModelConfigUpdate` independently
updates any of the six Companion model roles; it never modifies CharacterAgent roles.

See the [runnable example](../examples/12_character_companion/chat.py),
[HTTP contract](../../Documentation/Agents/Companion/Frontend%20API.md), and
[operations](../../Documentation/Agents/Companion/Companion.md).

Agent `LLMModelTarget` values accept `web_search_enabled`. Companion uses only
its dedicated `model_companion_web_search` target (or configured JSON provider)
for normal router-selected Web retrieval. Legacy per-turn
`tools.web` is accepted but ignored; `tools.allowed` still restricts Web. The provider receives bounded router-generated
queries without public/private filtering. Routing, image interpretation,
synthesis and rapport disable provider-managed search; only the dedicated Web
adapter performs it. See [shared agent web search](../../Documentation/Architecture/LLM_WEB_SEARCH.md).

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

## Foundry snapshots

`send(..., foundry_context=snapshot)` forwards an optional version-1 dictionary
without changing its Foundry/Shrecknet identifiers. Check
`(await api.capabilities()).get("foundry_context", {}).get("versions", [])` for
version 1 before submitting. Snapshot text is client-provided context, not verified
Foundry state. The optional argument is omitted for existing callers.

The same snapshot and idempotency key must be retained on ambiguous transport
failure; do not regenerate timestamps while retrying. A deliberately new question
gets a fresh snapshot and key. Table/World scope is separate from the speaking
CharacterAgent. See the [complete typed contract](../../Documentation/Agents/Companion/Frontend%20API.md#foundry-context-snapshots)
for limits, errors and source manifests, and the
[runnable table-context example](../examples/12_character_companion/foundry_context.py).

### Inspect answer origins

Completed `turn()` results preserve `result.tool_calls`, `result.evidence` and
`result.segments` without transformation. `messages()` includes nullable
`answer_details` for each assistant reply, with `tool_calls`, `evidence`, `segments`,
`coverage` and `warnings`; old replies and user messages return null. Match each
segment's `evidence_ids` to evidence records to inspect excerpts, references and
structured data, then match tool call `evidence_ids` to see which retrieval supplied
them. New answers have one `conversation` segment for the whole answer: its citations
do not establish claim-level support, and optional source markers may be absent.
Coverage is `unknown` unless source failure or partial Foundry context makes it
`partial`; older answers retain their original segment types and coverage.
`returned_count` may exceed the supplied IDs when the evidence budget excludes
results. `cache_hit` indicates reuse; a query on such a call is the current request,
not a fresh search. These are owner-only historical snapshots, not proof of semantic
grounding or continued source access. See the canonical [API contract](../../Documentation/Agents/Companion/Frontend%20API.md#answer-tool-and-evidence-breakdown).

ShreckWorld tool calls retain `source_coverage` counts. Returned indexed passages
keep `status="ok"` even when other indexes are missing; `source_readiness="partial"`
and optional `reason` of `incompatible_index`/`index_not_ready` signal limited
coverage. No passages are `empty`; service/transport failures remain `unavailable`
or `timed_out`. `sources_requiring_attention` identifies book IDs/titles and
`index_not_ready`/`incompatible_index`/`empty_index` reasons. Reindex affected sources
before relying on full coverage; uncited books are not automatically unindexed.

## Evidence-first pipeline

The evidence-first pipeline is the sole runtime path; the retired pipeline
selector is not accepted through `/config/`. SDK model configuration includes `model_companion_web_search` and `model_companion_evidence` alongside
the existing four roles. The web target defaults to OpenRouter DeepSeek Flash with
search enabled; per-turn `tools.web` is accepted for backward compatibility but is
ignored; configured Web can run alongside other router-selected sources.

`send()` additionally accepts `general_knowledge_opt_in`, `use_page_scope`, and
`clarification_id`. For example, after reading the chat's pending clarification:

```python
chat = await sdk.character_companion.chat(chat_id)
pending = chat.get("pending_clarification")
turn = await sdk.character_companion.send(
    chat_id, "Use Ravenloft", selection_version,
    idempotency_key=str(uuid4()),
    clarification_id=pending["id"] if pending else None,
)
```

`general_knowledge_opt_in=True` permits labeled unverified content
only when the server policy allows it. `use_page_scope=True` releases a saved
conversational override; submit the desired page `context` with that turn.

Chat reads retain resolved ontology, scope override and pending question metadata.
Message dictionaries include permanent `commentary` records in ordinary history;
never discard them on `response.final`. Turn snapshots add stage and commentary.
Events add `pipeline.status`, `message.committed` and `ontology.updated`; the
existing event parser preserves their names and payloads. Factual drafts stay
private until deterministic citation and required access checks. See the [canonical API](../../Documentation/Agents/Companion/Frontend%20API.md#evidence-v2)
for migration, replay, limitations and failure behavior.

## Rich responses

`turn.result` preserves complete `text` and additive raw presentation fields.
`turn.rich_response` decodes supported version-one envelopes into generated typed
models; it returns `None` for absent, unknown or malformed presentation while
retaining the raw result/text. Messages and final event payloads retain
`answer_details.rich_response` and `rich_response` respectively. Types are also
exported as `CompanionRichResponse` and `CompanionPresentationConfig`.

Administrators can read `presentation_config()` or call
`update_presentation_config(CompanionPresentationConfig(companion_rich_response_enabled=True))`.
Only explicitly set fields are sent. New/missing backend settings enable rich
responses by default, preserving saved false values; web images have a separate default-off JSON-provider capability
opt-in. No new model role is needed. Deploy matching services/workers/clients together.
The SDK conservatively reports rich responses disabled when an older server
omits presentation settings.

`rich_asset_content(turn_id, asset_id)` downloads saved owner-only Atlas raster
bytes with the current SDK authentication, preserving normal HTTP errors. The
ShreckWorld client/service owns book media and Foundry resolves local UUID images;
never attach service tokens to an external asset URL. Read the runnable
`examples/12_character_companion/rich_responses.py` for safe saved-turn inspection.
The [frontend contract](../../Documentation/Agents/Companion/Frontend%20API.md#rich-response-v1)
defines block/asset/navigation fields and compatibility.

## Retrieval recovery and long-running turns

The server plans light/deep retrieval and relevant character memories; clients do
not construct nested retrieval plans. Atlas normally reuses the Companion query,
and individual book questions use ShreckWorld single evidence retrieval. Identity
and traits shape voice even when memory retrieval is skipped.

Web runs as a normal selected tool concurrently with internal sources. Any
router-generated query may execute when `tools.allowed` permits Web, with at most
two queries and five retained Web records. There is no public-query catalog,
privacy filter, assessment or separate memory vote. Each internal tool retains
at most five records; synthesis selects at most twenty within its token budget.
Full admitted evidence/navigation is retained even when omitted from model context.

Calls default to a maximum of 600 seconds each. Total turn execution may span
several calls; `send` still returns admission immediately. `events()` already uses
an unbounded stream-read timeout. Reconnect or poll the existing turn ID instead
of submitting again. Authentication expiry and cancellation remain authoritative.

Raw `CompanionTurn.result` preserves additive `tool_calls[]` fields:
`retrieval_depth` (`light`/`deep`), `timeout_s`, and nullable `timeout_layer`
(`tool`/`http`/`retrieval_round`). Inspect `result.diagnostics` for deadline and
tool, citation and truncation outcomes. Older records omit these fields; use `.get()` defaults, as in
[inspect_answer.py](../../python_sdk/examples/12_character_companion/inspect_answer.py).
Startup migrations now upgrade shorter saved Shrecknet/gateway timeouts once;
later administrator edits remain authoritative. See the
[coordinated configuration](../../Documentation/Agents/Companion/Model%20Configuration.md#ten-minute-call-rollout).

## Markdown generation and failures

New answers return readable Markdown and one `conversation` segment. Numeric
source aliases resolve only against the evidence supplied to synthesis. Unknown or
malformed markers create warnings and no invented links. Citation validity is not
proof of semantic grounding. `coverage` describes retrieval/context limitations.

Length termination retries once with identical evidence and shorter-answer guidance.
Persistent truncation or provider/empty-output failure is a typed failed turn, never
an evidence refusal. Required source access/revision failure prevents publication;
optional rich/media failure preserves cited text. Missing evidence may produce an
honest completed limited answer. Asynchronous rapport still follows completion.

New results have `assessment:null`, `rejected_claims:[]`,
`diagnostics.version="markdown_v3"`, optional truncation/media diagnostics and
`citation_validation` counts. SDK raw dictionaries preserve older assessment,
segment and coverage shapes without rewriting history. `inspect_answer.py` reads
both historical and current diagnostics. Model configuration retains six keys;
`model_companion_evidence` and `model_companion_interim` are inactive compatibility
roles. No new SDK arguments or database migration are required.
