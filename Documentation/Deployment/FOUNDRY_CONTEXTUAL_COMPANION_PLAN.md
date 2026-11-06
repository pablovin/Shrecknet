# Foundry contextual Companion chat plan

Status: proposed; no runtime implementation. Reviewed on 2026-11-06 against the
current working tree, including pending Foundry workspace changes.

## Goal and feasibility

Extend the existing Character Companion into a compact chat sidecar that can
discuss the current table and explicitly attached Foundry documents. The first
release must work independently of the installed game system. Table name and
description, the selected Shrecknet World, and Foundry system metadata provide
useful grounding without requiring a system-specific mechanics adapter.

This is feasible with the current module and Companion backend. It requires an
additive message contract and context processing in the existing Companion job,
as well as UI work. It does not require another agent or game-state synchronization.
Precise mechanical analysis across arbitrary systems and fog-safe scene capture
remain separate capabilities requiring runtime validation.

The module manifest currently targets Foundry v14 minimum/verified. Scope this
work to v14; do not imply v13 compatibility from the availability of similar hooks.

## Existing foundations and gaps

| Area | Current behavior | Planned extension |
| --- | --- | --- |
| Chat UI | `CompanionWorkspace` inside `ShrecknetShell`, saved tabs, drafts, history, cancellation | Compact sidecar sharing the same conversation controller |
| Admission/recovery | Stable idempotency keys, private durable turns, two-second snapshot polling | Preserve behavior with an immutable context snapshot per admitted message |
| Context | Sends `context: {surface: "global"}` | Visible table grounding and explicit document snapshots |
| Backend contract | Strict `PageContext`; only Shrecknet surfaces and references | Separate typed Foundry context envelope |
| Sources | Structured evidence, citation validation, saved message manifests | Client-provided Foundry sources with safe document navigation |
| Integration | World-scoped `worldId` and `worldName`; each player signs in | Resolve selected World metadata using that player's existing client |

Two existing mismatches matter for implementation:

- `PageContext.world_id` accepts a positive integer, while `World.id` and the
  configured Foundry `worldId` are strings. Do not coerce World identifiers into
  integers. Add a typed string reference in the new envelope; audit legacy
  `PageContext.world_id` callers before any independent compatibility repair.
- The canonical `World` model currently contains `id` and `name`, not a description.
  Table description should come from available Foundry world metadata, sanitized
  for the requesting user. Missing descriptions are acceptable. Adding a Shrecknet
  World description field would be a separate schema change, unnecessary here.

## Chat experience

1. Add a Companion launcher that opens or focuses one compact resizable sidecar.
   Keep the existing four-menu workspace and saved conversations available.
   Use normal Foundry window placement; reserve hard docking for a later need.
2. Put a small companion identity row, conversation picker, scrollable messages,
   context chips, optional quick questions, and a persistent composer in the panel.
   The large portrait and chat administration controls should not consume its
   default reading area. Preserve keyboard focus and the draft during polling.
3. Show a table context chip by default, expandable into the exact information
   being attached. Allow removal for the next question. Include the active player's
   assigned Actor as a visible optional attachment, never silently select an NPC
   from the user's current token selection.
4. Add **Ask Companion** to supported Actor, Item, and Journal windows. It opens
   the sidecar, attaches the document, and focuses the composer without submitting.
   A Journal action attaches the current readable page, not the entire journal.
5. Accept Actor/Item/Journal page drag-and-drop and explicit document links through
   the same resolver and permission filter. Deduplicate chips by document UUID and
   selected section. Unsupported content gets a clear explanation.
6. Offer context-specific draft suggestions such as **Explain this item** and
   **Summarize this page**. Initially quick questions populate the composer; Send
   remains the single submission action.
7. Keep attachments for follow-up questions within that chat until removed. On
   Send, recheck access and refresh the snapshot, then freeze it for admission.
   Mark changed or unavailable attachments. If one becomes unreadable, block Send
   until it is removed or access returns; never transmit its cached content.
8. Preserve pinned references and drafts while moving between Foundry windows.
   Opening another window may suggest an attachment but never silently adds it.
   Use separate state per server/Foundry world/Shrecknet account/chat. Clear local
   private state on sign-out or connection change. Do not restore raw snapshots
   from browser storage; keep pending references in memory initially.
9. Render source cards beneath each answer and the submitted context manifest
   beneath each user message. Removing a chip affects future turns, not saved
   history or knowledge already present in that conversation. A new chat starts
   without the prior transcript.

The selected Companion CharacterAgent and the player's Foundry Actor have different
roles: the former supplies the speaking identity and rapport, the latter is game
context. Attaching an Actor must never change the global Companion selection.

## Context extraction without system dependence

Use a focused module-owned context bridge: resolve, check permissions, extract,
sanitize, bound, preview, package. Do not walk arbitrary `system` or `flags` trees.

| Context | Initial content | Limitations |
| --- | --- | --- |
| Table | Foundry world ID/title/available description; Foundry version; system ID/title/version; selected Shrecknet World ID/name | Description is background data, not a rules definition or an instruction |
| Actor | Permitted identity/type, readable descriptive content and labeled sheet values, readable owned item summaries | Generic extraction cannot promise every ability or interpret every resource |
| Item | Permitted identity/type, readable description and labeled sheet properties, permitted owner reference | Do not infer undocumented mechanics from internal field names |
| Journal page | Current readable page or explicitly selected visible text, headings, permitted link references | Exclude unreadable pages, secret blocks, hidden editors and unrelated DOM |

Use core document metadata where safe. For system-defined details, prototype
extraction of visible labeled sheet content within the selected sheet's content
root. Exclude forms with hidden inputs, private/GM sections and inactive hidden
panels. Raw `textContent` over an application is insufficient. If meaningful safe
labels cannot be recovered, attach only safe metadata and visible descriptions,
and mark coverage partial. Native document extraction can cover additional data
only once its visibility and field meanings have been established.

System-specific adapters can improve coverage later, but are not a prerequisite
for launching chat. Test the generic path on multiple systems and a custom sheet.
Explain missing mechanics rather than inventing values or silently exporting JSON.

## Proposed backend contract and ownership

Keep `POST /users/me/character-companion/chats/{chat_id}/turns` and add optional
`foundry_context` to `TurnCreate`, alongside the existing `context` and image
`attachment_ids`. Old payloads remain valid. Add a capability descriptor for the
supported context version and limits; older backends retain text chat and clearly
disable contextual submission rather than silently dropping attached context.

Proposed envelope version 1:

- `version`: exactly `1`.
- `table`: nullable typed object with `foundry_world_id`, `title`, nullable
  `description`, `foundry_version`, `system_id`, `system_title`, `system_version`,
  nullable `shrecknet_world_id` and `shrecknet_world_name`.
- `documents`: ordered typed records with `uuid`, document `kind` restricted to
  Actor/Item/JournalEntryPage, `name`, `captured_at`, nullable `source_revision`,
  nullable `parent_uuid`, `coverage` (complete/partial/unknown), sanitized `text`,
  bounded labeled scalar `fields`, and permitted link references (`uuid`, `label`).
- `captured_at`: UTC timestamp for the overall snapshot. Client timestamps and
  source revisions are provenance claims, not server-verified freshness.

Initial proposed limits: six documents, 8,000 text characters per document,
4,000 description characters, 100 scalar fields per document, 80-character labels,
500-character string values, 20 links per document and 64 KiB for the serialized
envelope. Validate UUID/string lengths and numeric finiteness; reject unknown keys
and excess payloads. Integrate Foundry material into the existing evidence/token
budget. Expose omissions in the preview; do not silently truncate mechanical facts.
These are proposed starting limits, to be confirmed with representative tables.

Implementation ownership:

- `scripts/main.js`: register launch/header/context hooks once; identify supported
  document windows and delegate to the sidecar/context resolver.
- `scripts/apps/character-companion.js`: retain the conversation controller;
  separate shell-only navigation/render assumptions before adding another view.
  Reuse the controller and a shared chat template from both surfaces, with one
  active controller and polling lifecycle per signed-in session.
- New focused module context helpers: table metadata, document resolution,
  extraction, visibility filtering, preview and limits. Keep HTTP in
  `scripts/api/shrecknet-client.js`.
- `app/schemas/character_companion.py`: strict envelope and capability contracts.
  `CharacterCompanionService`: admission, validation, snapshot manifest and storage
  orchestration. Existing turn request JSON can store the snapshot; existing
  message manifests can store safe history references without new tables.
- `app/jobs/companion/`: convert snapshots into bounded evidence; adapt routing,
  synthesis and validation with synchronized prompt contracts. Add a distinct
  Foundry source type, categorized as `user_provided`. Sources must say **Foundry
  snapshot**, not imply server verification or current live access.
- Persist the snapshot once at admission. Exact retries reuse the original body
  and idempotency key even if Foundry has changed. A deliberate new question gets
  fresh extraction and a new key. Never refresh payloads during ambiguous retries.

No direct live lookup channel from backend to Foundry is required for this phase.
Update the SDK `CharacterCompanionAPI.send` to accept the optional envelope, plus
API documentation, SDK examples and capability/validation tests in the same change.

## World grounding and retrieval

Pass the selected Shrecknet World as context, separately from the Foundry table
identifier and the speaking character's ontology. Resolve the configured World
using the signed-in player; setup keys and World association do not grant access.
Do not send setup credentials, shared tokens or all ontology contents.

Current Atlas retrieval takes one `ontology_id`, with the selected Companion's
ontology as fallback; it does not implement arbitrary multi-ontology World search.
Therefore World metadata grounding can ship first, without claiming that all World
lore is searchable. A subsequent explicit World scope resolver can select authorized
ontologies and reuse the existing retrieval executor, with bounded fan-out and
character memory still tied to the speaking CharacterAgent.

Likewise the bookshelf's `shrecknet_world_id` association can help discover matching
ShreckWorld libraries, but the Companion currently expects a `shreckworld_id`.
Support an explicit authorized library selection when several match. Do not guess
the rules edition from the World name or enable every library automatically.
Chronicle `game_id` is not a Foundry world mapping; use authorized explicit Chronicle
references until a separate association exists.

## Visibility, trust and citations

Perform Foundry permission and visible-content filtering before transmission and
again before each fresh Send. Resolve UUIDs locally under the current user.
Document access does not imply access to every field or unrevealed secret.
Do not fetch linked documents recursively or transmit hidden enemy statistics.

The backend can validate bounds, ownership of chats and Shrecknet source access;
it cannot independently attest a browser's Foundry permissions or a claimed GM
role. Treat snapshots as untrusted user-supplied evidence and document that trust
boundary. Foundry descriptions, journal content and labels cannot override agent
instructions, grant tools or widen retrieval permissions.

Render model text escaped as today. Build clickable Foundry source controls from
validated submitted references, not arbitrary model HTML or invented UUIDs. Resolve
and check access again on click; handle deleted documents and a different current
table. Preserve separate labels for Foundry observations, rulebook passages,
Atlas records, Chronicles, subjective memories and tactical suggestions.

## Delivery order and acceptance

| Milestone | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1: Chat sidecar | Compact shared chat view, launcher, visible table/World context and typed backend support | Same saved chats and identity; no duplicate sends/pollers; name/description reach synthesis; text-only clients still work |
| 2: Document context | Actor/Item/Journal actions, drop attachments, preview/removal, generic extraction and citations | Opening attaches without sending; fresh snapshots per new turn; secrets excluded; partial coverage shown across systems |
| 3: Retrieval grounding | Explicit authorized World/ontology and rules-library scope selection | Correct World despite a Companion from another ontology; unauthorized or ambiguous sources handled explicitly |
| Later: Situation | Token/combat/roll context, then optional viewport image | Prototype player visibility, fog, hidden identities and image capture before release |

The original fight/tactics examples need the later situational milestone to discuss
the actual encounter. Milestones 1–2 can explain the attached character/item/page
and answer from table background, while clearly stating unavailable combat facts.
Arbitrary-window capture, whole-scene screenshots, live assistant lookups and
game-state modifications are deferred.

Required automated coverage: table metadata with missing description; separate
Foundry/Shrecknet IDs; multiple game systems and safe extraction fallback; secret
and hidden-field exclusion; inaccessible/deleted drops; context deduplication and
limits; invalid envelopes and unsupported capability; immutable retry bodies;
chat/account/world isolation; shared sidecar/workspace state; cancellation and
reconnect; snapshot manifests after reload; safe citation navigation; unchanged
rapport and chat quota behavior; scope resolution and authorization failures.

Required manual v14 checks: standard and custom sheet header actions, current
Journal page detection, drag/drop, narrow windows, focus/scroll stability, switching
chats during a running reply, two Foundry tables, GM/player visibility differences
and explicitly excluded secret text. Node mocks cannot certify these behaviors.

Update the canonical Foundry guide, Companion behavior/prompt flow and frontend API,
SDK docs/examples, and release notes when implementing. Deploy capability/schema
support and workers before the updated module. No new SQL tables or migrations
are expected for snapshot JSON; audit cleanup paths so chat deletion clears it.
Retain additive backend decoding during rollback so saved Foundry evidence remains
readable. Disabling contextual submission must preserve existing text chat.

## Verification and references

Baseline: `node --test tests/*.test.mjs` from
`integrations/foundry/shrecknet-foundry/` passed 69 tests on 2026-11-06. This validates
the existing module contracts, not the proposed feature. No Foundry runtime,
provider quality evaluation or new backend integration tests were run for this plan.

- [Current Foundry behavior](FOUNDRY_MODULE.md)
- [Current Companion pipeline](../Agents/Companion/Companion.md)
- [Companion frontend API](../Agents/Companion/Frontend%20API.md)
- [Foundry v14 header controls](https://foundryvtt.com/api/v14/functions/hookEvents.getHeaderControlsApplicationV2.html): supports adding header menu controls; an always-visible icon needs UI validation.
- [Foundry v14 document context menus](https://foundryvtt.com/api/v14/functions/hookEvents.getDocumentContextOptions.html): document-specific menu extension hooks.
- [Foundry v14 UUID resolution](https://foundryvtt.com/api/v14/functions/foundry.utils.fromUuid.html): resolves local document references; resolution alone is not an authorization check.
- [Foundry v14 TextEditor](https://foundryvtt.com/api/v14/classes/foundry.applications.ux.TextEditor.html): drag payload helpers, document links and secret-filtering enrichment options; these are building blocks, not a complete extraction filter.
