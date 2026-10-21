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

The worker pipeline is:

1. Recheck originating user/session and canonical identity; load pair scores and
   up to twelve committed messages from this chat.
2. Stage 1 deterministic routing, optionally assisted by the routing model for
   ambiguous requests. Optional routing failure falls back to conversation.
3. For image turns requiring retrieval, interpret the image into bounded visible
   observations and search terms. Interpretation is not published as an answer.
4. Retrieve allowed sources concurrently with independent tool timeouts and an
   overall deadline. An optional interim model can react concurrently using only
   identity and the question. Its output is provisional and excluded from rapport.
5. Stage 2 streams one embodied structured synthesis. Text deltas are provisional.
   Validate segment types, supplied citation IDs, current source access/revisions,
   originating session and character availability before committing the final.
6. Commit the delivered user/assistant exchange, final event and rapport outbox in
   one transaction. Stage 3 evaluates rapport separately with version/generation
   checks and an audit trail.

Prompt contracts live beside the job in `jobs/companion/prompts.py`. Only Stage 2
produces final public output. Deterministic validators enforce structural citation
integrity; they do not prove semantic grounding or character fidelity.

Atlas uses the shared Elder retrieval executor without Elder synthesis. Character
memory uses the shared CharacterAgent ranking service and only the selected
character's perspectives. Memory is subjective evidence, not objective scene truth.
ShreckWorld uses evidence-only book retrieval. Chronicle search filters authorized
notes before ranking and uses text offsets/revision references; ShreckOS opens the
referenced body/reply passage and warns when the revision has changed. Optional web search
uses a configured JSON HTTP provider; it does not fetch arbitrary result pages.
Evidence is capped at twenty records and the configured estimated token budget.
The final context also has a bounded token estimate; model-specific context-window
fit and image token costs still require deployment validation.

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

Enable the optional Compose profile with
`docker compose --profile companion up -d shrecknet_companion_worker shrecknet_companion_rapport_worker`
(check service names against the current Compose file). Configure synthesis and
rapport models before enabling both `enable_ai_agents` and `companion_enabled`.
Routing/interim models are optional and have separate enable switches. See
[model configuration](Model%20Configuration.md).

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

- [Validation and deployment acceptance](Validation.md)
- [Frontend HTTP and SSE API](Frontend%20API.md)
- [Companion model configuration](Model%20Configuration.md)
- [Design and acceptance plan](Companion%20Backend%20Plan.md)
- [Python SDK](../../../python_sdk/docs/character_companion.md)
