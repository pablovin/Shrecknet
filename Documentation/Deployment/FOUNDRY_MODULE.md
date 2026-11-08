# Foundry module deployment

## Purpose

This page describes how to develop, release, install, and operate the
`shrecknet-foundry` module. The module supports Foundry VTT 14 and later and is
packaged independently from the rest of the Shrecknet monorepo.

## Production origin and CORS

The production Foundry installation is served from:

```text
https://foundry.shrecknet.club
```

Shrecknet and ShreckWorld read CORS only from the root `.env`, mounted read-only
into their API containers. Include `https://foundry.shrecknet.club` in
`CORS_ALLOW_ORIGINS`. Use the [shared browser CORS guide](BROWSER_CORS.md) for the
complete settings, initial container rollout, restart behavior, and rollback.
ShreckRPG is maintained separately; Chronicles still requires that service's
existing CORS setup until it adopts the same shared loader.

## Integration setup

1. A Shrecknet administrator creates a setup key with
   `POST /config/integrations/foundry` and optionally restricts it to World IDs.
   Set **Default World** in ShreckOS administration to bind new setup to one World.
2. A Foundry GM opens **Game Settings → Configure Settings → Shrecknet Connection**.
   Enter the Shrecknet API URL and setup key, then choose **Continue · choose World →**.
   Select the World (the administrator default is preselected when set). Service
   URLs default to the same host; **Advanced · service URLs** exposes overrides.
   Choose **Test all connections**, review each result, then **Save connection**.
   On the production proxy, use `https://shrecknet.club/backend_api`; the service
   defaults are `/shreckworld-api` and `/api/shreckrpg` on the same origin.
3. The enabled connection is checked immediately and every five minutes while a GM is connected. The panel records the shared online/offline status and permits a manual check.
4. The module never persists the setup key in a Foundry World setting. The GM can choose **Update World connection** to re-enter the key and apply a new administrator binding. The saved connection remains until its replacement is enabled.
5. Each player opens the **Shrecknet Worlds** globe control, signs in with their own Shrecknet account, and remains signed in on that browser until they use **Sign out**, the token expires, or it is revoked. ShreckWorld and ShreckRPG requests use that player's bearer token; do not configure a shared service token in Foundry.
6. A GM can edit **Advanced · service URLs** on the saved-connection screen. Run **Test all connections** again, then **Save changes**. URLs must use HTTPS outside localhost and cannot contain credentials, query parameters, or fragments.

The default 560×580 setup window uses a scrolling content region and a fixed
footer. It is constrained to the browser viewport; long results, service URLs,
and advanced fields scroll inside the window without requiring a resize.

### Setup checks and saving

The initial Continue action validates the key and loads permitted Worlds without
saving settings. **Test all connections** revalidates that key and World selection
(including any administrator default change), and independently requests
`GET /health` from Shrecknet, ShreckWorld, and ShreckRPG. Every check has its own
result. A health response must be JSON with `status: "ok"`; a website HTML page,
non-success HTTP status, timeout, or browser/CORS failure prevents saving.
Each HTTP probe times out after ten seconds. These requests use no player token;
the setup key is sent only to the Shrecknet credential endpoints. Health checks
prove browser reachability and service liveness, not player content permissions,
LLM readiness, or successful generation. Each player authenticates separately.

Save is enabled only after every check succeeds for the exact draft settings.
Editing a World or service URL disables Save until a new successful test. Nothing
is saved by Continue or Test. Failed checks retain the selected World and service
URLs for correction/retry. Existing saved connections remain until a replacement
is explicitly saved; **Keep saved connection** cancels reconfiguration. Updating
a World on the same server preserves existing custom service URLs. Saved-connection
tests check all three health URLs without requiring the discarded setup key;
the periodic five-minute monitor continues checking Shrecknet only.

The setup key lives only in window memory between Continue and Save. It is never
included in rendered context or Foundry settings, and is discarded on Save,
Back, cancel, or close. Closing the window ignores pending setup responses.
`shrecknet-connection-settings.js` owns this wizard and its draft/check state;
`connection-settings.js` owns persisted settings and `shrecknet-client.js` owns
HTTP. The content workspace does not own GM setup actions. No API, SDK, or
persistence contract change is introduced by this wizard update.

Open the workspace again after changing service URLs. The module replaces its cached
workspace when the server, World, service URLs, or Foundry user changes.

Chronicle access and sharing follow ShreckRPG authorization and capability
responses. Saves include the expected revision; on a conflict, reload before
editing again. Chronicle `game_id` is not associated with a Foundry World.
Protected Chronicle image assets are copied to Foundry-managed storage before
being shown to players or posted to chat.

The workspace has exactly four menus: **Atlas**, **Shreckworlds**,
**Chronicles**, and **Companion**. Atlas opens the configured World directly.
Shreckworlds opens its linked bookshelves without another World picker.
Chronicles follows the signed-in account's accessible documents, independently
of the Foundry World. Companion uses the account's global character selection.
The sidebar becomes an icon rail in Foundry windows at or below 760px; every
icon retains its accessible label and tooltip. Elder and rules-question panels
have no visible entry points in this workspace.

See [Foundry Integration API](../API/FOUNDRY_INTEGRATION_API.md) for the full
credential lifecycle and endpoint contracts.

## Local development

Place or symlink the module directory into Foundry's user-data directory:

```text
<Foundry UserData>/Data/modules/shrecknet-foundry/
```

The resulting directory must contain `module.json` directly at its root. Reload
Foundry, enable **Shrecknet** for a test World, and configure the Shrecknet API
origin as above.

## Release process

The module version is **0.4.0**, including contextual Companion chat, the four-menu
workspace and guided World setup. See the [release notes](../../changelogs/foundry-v0.4.0.md)
for rollout requirements and known limits. The release workflow uses the matching
`changelogs/foundry-vX.Y.Z.md` as its GitHub Release notes.

To build an installable archive locally from the repository root:

```bash
node --test integrations/foundry/shrecknet-foundry/tests/*.test.mjs
mkdir -p dist/foundry-v0.4.0
(cd integrations/foundry/shrecknet-foundry && zip -qr ../../../dist/foundry-v0.4.0/shrecknet-foundry-v0.4.0.zip . -x '.DS_Store' -x '__MACOSX/*')
cp integrations/foundry/shrecknet-foundry/module.json dist/foundry-v0.4.0/module.json
```

Use a fresh output directory when rebuilding, so obsolete files cannot remain in
an existing ZIP. Extract the ZIP into `Data/modules/shrecknet-foundry/` and reload
Foundry for a manual installation. The archive contains `module.json` directly at
its root. A local build does not publish the download URL in that manifest;
Foundry's URL-based install/update flow requires the GitHub assets below.


The stable installation manifest is:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

For a release `X.Y.Z`:

1. Update `integrations/foundry/shrecknet-foundry/module.json` so its version is
   `X.Y.Z` and its download URL is
   `https://github.com/pablovin/Shrecknet/releases/download/foundry-vX.Y.Z/shrecknet-foundry-vX.Y.Z.zip`.
2. Commit and merge that manifest change to `main`. Changes to the manifest on
   `main` trigger the release workflow automatically. Alternatively, create and
   push the matching `foundry-vX.Y.Z` tag on a prepared release commit.
3. The workflow derives the release tag from the manifest and verifies an explicit
   tag against it. For a branch-triggered release, GitHub creates the tag at the
   exact checked-out commit, never a later moving branch head.
4. The `Release Foundry module` workflow validates the version and manifest,
   builds a ZIP containing only module-root files, and creates the GitHub
   Release with `shrecknet-foundry-vX.Y.Z.zip` and `module.json` attached.
   Validation runs the module tests, including checks that every script, style,
   and README referenced by the manifest exists.
5. Verify the release ZIP and `module.json` assets are publicly downloadable
   before installing or updating from the stable manifest URL through Foundry's
   **Install Module** dialog. The raw manifest tracks `main` and can advertise
   a version before its release has been published. To install a specific
   published release, use
   `https://github.com/pablovin/Shrecknet/releases/download/foundry-vX.Y.Z/module.json`.

A `Not Found` error for the ZIP means the advertised release asset is unavailable.
A version bump alone does not publish the ZIP: merge the change, push its matching
tag, and confirm the release workflow succeeds. In particular, validation must
check the manifest's actual files rather than a removed theme metadata file.
Version `0.3.1` removes that obsolete release check. No backend migration or
configuration change is required. To roll back, restore a previously published
module ZIP; never repoint an existing release tag.

The release tag is immutable. Do not move or recreate it after publishing.

## Retained Elder transport and interrupted connections

The specialist Elder client remains for compatibility and tests, with no visible
entry point in the four-menu workspace. The following describes its transport.

The Elder workspace displays a successful query's `answer` and sources directly
in the submitted conversation. It does not depend on a subsequent chat-list
refresh or switch to the first listed chat.

Before submission it snapshots that owned chat's history. If the query request
loses its connection or receives gateway 502/504, the plugin keeps the question
pending and checks `GET /jobs/elder/chats/{chat_id}?include_history=true`
immediately, then every three seconds, up to 200 checks (about ten minutes).
Only a new matching user question followed by an assistant message counts as a
completed reply. Previous answers and incomplete turns are not reused. A 500
response gets one immediate history check; validation errors are not polled.
Recovery never repeats the POST or starts another generation. Unauthorized,
forbidden, and missing-chat responses stop recovery. Leaving the conversation,
closing the workspace, or signing out prevents late replies from updating it.
Sending another question and changing Elders are disabled while a question is
pending. If no completed reply is found, reopen that conversation to check its
history before resending: the backend may still be generating.

Elder query routes return one JSON response after generation; there are no SSE
heartbeats. Configure the Foundry-reachable API proxy to allow long Elder query
requests (900 seconds is the ShreckOS bundled proxy's setting), and return CORS
headers for the Foundry origin on gateway errors as well as successful replies.
An upstream HTTP error without those headers can appear as the generic browser
message **Unable to connect to the configured service**. The plugin can recover
saved replies, but cannot change an external proxy's timeout or CORS policy.

No backend response, authentication, or database contract changes are required.
These fixes remain in the `0.3.2` plugin release.

## Atlas Explorer

Atlas directories show complete entity-type icons, `name`, and owning ontology
names. Cards grow with wrapped labels instead of inheriting Foundry's fixed
button height. Page and related-record cards also grow to keep names visible.

Opening an Atlas page presents a compact header with its name, types, thumbnail,
and **Show in chat** action. The reading area offers **Original text** and
**Generated text** per entity section. Original text is selected by default when
both sources exist; a section with only one source uses that source. Selections
persist in the current workspace session while navigating Back and Forward.
Sections without either source display an empty-state message.

Relationships are grouped by relationship definition, with each target displayed
as its **page name · entity type**. The page name takes precedence over the target
entity alias. Targets are deduplicated by page and type within each group across
all sections. A target may still appear under multiple relationship types.
Unresolved targets show **Related page unavailable**, without internal IDs.
Relationship targets and ordered Narrative Scenes open through the existing
workspace navigation and history. Related content remains available separately.

At workspace content widths of 1100px and above, navigation, reading, and
properties occupy three columns. From 750px, collapsible panels share a column
beside the reading area. Below 750px, panels stack above the reading area. Panels
start collapsed when first opened below 1100px and remember user toggles during
the workspace session. **Focus reading** hides both panels; **Show panels**
restores them. Breakpoints follow the resizable content area, not browser width.

### Show in chat

The single page-level button creates one Foundry chat message containing:

- The page name and image, when available.
- Every entity section, with only its selected original or generated text and a
  source label. Empty sections are identified without preventing sharing.
- Relationships grouped by type, with target page names and entity types.

Only the explicitly shared page's content is included. Related pages' body text,
properties, scenes, and recursively connected content are not added. The message
is a snapshot, not a live Shrecknet page or a set of Foundry document links.
Chat posting uses the current Foundry user's speaker and default message
visibility; the action does not impose an additional GM-only restriction.
The sender must therefore choose content appropriate for that chat audience.
Duplicate clicks while a share is pending create only one message, and failures
are reported in the workspace.

Atlas sharing uses the same safe HTML subset as Chronicles: scripts, event
handlers, unsafe links, and unsafe embedded images are removed. The Atlas page
view offers **Show in chat** as its sole content-sharing action. Image reveal
controls elsewhere in the module retain their existing behavior.

### Ownership and compatibility

`scripts/apps/atlas-content.js` owns source selection, relationship grouping,
and chat markup. `shrecknet-shell.js` orchestrates existing API reads and Foundry
chat creation; `shell.hbs` and `styles/base.css` own presentation. The shared
`_presentDocument` sanitizer also backs Chronicle rendering and protected asset
handling. Chronicle asset resolution remains scoped to its owning Chronicle.

Type labels use `GET /ontologies/{ontology_id}/entities` without the directory's
`display_on_world=true` filter, so referenced hidden types still have names.
Relationship labels use
`GET /ontologies/{ontology_id}/entities/{entity_id}/relationships` for the page's
entity definitions. Metadata is cached within the workspace by ontology;
relationship targets continue to use the existing entity-resolution endpoint and
signed-in player's authorization. A failed metadata read is not cached as a
success and can be retried. Close and reopen the workspace to refresh cached
metadata after ontology changes. Signing out clears cached Atlas pages, resolved
relationships, metadata, and text-source selections before another user signs in.

Version `0.3.2` includes these changes. No public API or SDK contract, database
migration, or configuration change is required. Publish the matching module
release using the process above; rollback uses the previous published ZIP.

## Character Companion

The left menu contains **Atlas**, **Shreckworlds**, **Chronicles**, and
**Companion**, in that order. Narrative Scenes and the existing Elder workspace
remain accessible from Atlas home and Atlas page relationships/scenes.
**Companion** uses the canonical Character Companion API; it is independent of
Atlas Elders and the ShreckWorld rules companion.

The Companion workspace presents a character hero card with a portrait fallback,
name, subtitle, availability, and character chooser alongside a full-height chat.
On narrow windows the card becomes a compact header. Saved conversations appear
as horizontally scrollable buttons with an announced selected state. Choose a tab
to read it; **New chat**, **Rename**, and **Delete** manage server-persisted chats.
Deletion requires an inline confirmation and preserves character memory and
rapport. Rename submits `expected_revision`; conflicts and quota errors are shown
without silently overwriting server state. The server enforces the account-wide
ten-chat limit; the composer shows current usage. Existing chats above that limit
remain readable. **Load earlier messages** retrieves chronological history using
the server's opaque `before` cursor.

**Change companion** expands the character chooser; **Conversation options**
expands rename/delete controls. Returned source titles and excerpts are available
in expandable source panels, rendered as escaped text. Refresh respects a
selection made in another client instead of reopening the old character.

Completed messages also show when ShreckWorld sources require reindexing or have
no ready index, including how many requested sources were searched. A partial
result can still cite passages from compatible sources.

The **Allow web search** control is selected by default when the Companion
capabilities report a configured provider; clear it to disable web search for
subsequent questions in that chat. It is disabled when web search is unavailable.
The choice is kept in memory per chat, not persisted across sign-out. Ambiguous
admission retries keep the original web permission and payload.

The character chooser updates the account's global selection with its current
optimistic version. Opening a chat for a different character also selects that
character; chats stay bound to their original character. Unavailable characters
cannot start chats or receive messages. Selection changes in another client may
produce a conflict; **Refresh** reloads current server state. Character choices
and chat lists follow all pagination cursors. All requests use the signed-in
player's bearer token and existing owner-only authorization, never the GM setup
key. No conversations are posted to Foundry chat or shared with other players.
Message text is escaped rather than rendered as trusted HTML.

The HTTP namespace is `/users/me/character-companion`. The module reads
`GET /selection`, `GET /candidates`, `GET /chats`, `GET /chats/{id}`, and
`GET /chats/{id}/messages`; it uses `PUT /selection`, `POST /chats`,
`PATCH /chats/{id}`, `DELETE /chats/{id}`, `POST /chats/{id}/turns`,
`GET /turns/{id}`, and `POST /turns/{id}/cancel` for changes and replies.
Chat/turn creation retains its UUID `Idempotency-Key` and exact payload across
ambiguous retries. Configure the Shrecknet browser CORS policy to permit
`PATCH`, `PUT`, `DELETE`, and the `Idempotency-Key` header as well as bearer auth.
See the [canonical HTTP contract](../Agents/Companion/Frontend%20API.md).

Replies use durable admission and two-second snapshot polling while the workspace
is visible. Provisional snapshot drafts are labeled **Reply in progress** and are never
presented as final output. Reopening or refreshing a running chat resumes its
`active_turn_id` without resending a question. **Stop reply** cancels the saved
turn. Turn failures are reported and leave history available. A transport failure
pauses polling; **Refresh** checks saved state before further sending. Changing
chat, character, or chat metadata is disabled during a running reply. Navigation
away pauses polling; returning resumes it. Closing pauses polling and fences late responses while retaining in-memory drafts
and context references for reopening. Signing out clears all local Companion state.
Neither action cancels server work.

`character-companion.js` owns this workspace's state and actions; the shared API
client owns HTTP and authentication. This Foundry view supports text
messages and the contextual snapshots below; image uploads, private attachment previews, rapport controls, and live
SSE deltas remain available through other Companion clients. Backend Companion
feature/model configuration must already be enabled. Companion uses existing
endpoints and requires no Companion schema migration. The setup default adds
its own nullable integration column. Rollback preserves backend chats.

### Contextual chat and compact sidecar

**Ask Companion** in the Shrecknet scene controls opens one compact resizable
window. **Compact chat** and **Expand** switch that same workspace between layouts;
there is one conversation controller and polling loop. The main Shrecknet launcher
restores the full layout. No separate agent, server connection or shared GM token
is created. Opening the cached window rechecks the browser's saved account and
connection identity. Account/connection changes create fresh state; authentication
expiry and sign-out clear all content caches. Late errors from replaced windows
cannot clear the new account's session.

The default **Table** chip includes the Foundry table title and available
sanitized description, installed system ID/title/version, Foundry version, and
configured Shrecknet World ID/name. **Review attached context** displays the
background and attached document previews. Toggle the table chip off to exclude
that background and its lore/rules selection from the next question. The speaking
CharacterAgent remains independent of any attached Foundry Actor.

Standard v14 Actor, Item, and currently viewed Journal page headers gain **Ask
Companion** in their header controls. Actor/Item/Journal page context menus also
provide the action. It opens/focuses chat and attaches the document without sending.
Drag documents or a UUID link into chat, use **Attach a document link**, or explicitly
attach the player's assigned Actor. A Journal link resolves its current page, or
its sole readable page; otherwise open the desired page first. Quick questions
populate the composer and require Send.

Chips can be removed independently. References remain attached to that chat in
memory until removed, sign-out, connection change, or browser reload. New chats
start with table context and no prior document attachments. Saved user messages
retain their submitted snapshot manifests; removing a chip affects future turns,
not the existing transcript. Send rechecks permissions and extracts a fresh
snapshot. Ambiguous retries preserve the original body, timestamp and key; they
never silently refresh the pending message. A definite validation rejection lets
the user edit again. Refresh can recover an already admitted turn.

The extractor is system independent. It reads safe core metadata and visible sheet
text, definition-list values and labeled readonly/disabled inputs. It excludes
unrevealed secret blocks, hidden DOM, password/hidden inputs, editable controls and
unrendered tabs, and never serializes `system` or `flags`. Journal text uses the
current page's sanitized document content. Actor/Item sheets that are closed or
unsupported fall back to metadata only; open the sheet to include readable text.
Every document snapshot has partial coverage. This does not promise complete
mechanics or combat awareness. Six documents, 8,000 text characters each, 4,000
characters of table description and 64 KiB per envelope are maximums; excessive
text is rejected rather than silently truncated. Structured fields and links have
additional API limits. The configured backend evidence-token budget can reject a
smaller snapshot with `413 foundry_context_budget_exceeded`.

Under **Review attached context**, **World lore** selects an ontology belonging to
the connected World. A sole ontology is used automatically; multiple ontologies
require an explicit selection and are never searched as an unbounded fan-out.
**Rules library** lists ShreckWorld libraries linked to that World and defaults to
none. Library discovery failure leaves chat available with a notice. A table name
or system name does not establish a rules edition. Character memory stays tied
to the speaking CharacterAgent. There is no implicit Chronicle game/table mapping
or campaign-note search in Foundry turns; the backend requires an explicit Chronicle
reference before routing that tool.

Source panels label Foundry material as client-provided snapshots. **Open document**
and attached link controls resolve only submitted references, checking the current
table and current permission before opening a sheet. Model text remains escaped.
The backend verifies Shrecknet World/ontology associations and chat ownership, but
cannot attest browser-side Foundry permissions, claimed timestamps or field values.
Snapshots and document instructions never grant tools or override agent policy.

The module reads `GET /users/me/character-companion/capabilities` and sends optional
`foundry_context` on the existing turns endpoint. Older servers retain text chat
with a notice and disable contextual transmission. Deploy the updated backend and
Companion worker before the module. Snapshot JSON and manifests use existing SQL
columns; no database migration or actor synchronization is required. Normal chat
purge removes snapshots. Module rollback retains saved chats; retain additive
backend decoding while Foundry sources remain in history.

Browser fixtures validate generic DOM filtering and layouts; real v14 table /
custom-sheet acceptance remains unverified. From the module directory, serve it with `python -m http.server 8831` and
open `http://localhost:8831/tests/foundry-context.browser.html` for repeatable native
DOM checks. Scene images, tokens/combat/roll extraction, automatic live lookups,
selected-text attachments and game-state changes are not implemented.

See the [implementation/design record](FOUNDRY_CONTEXTUAL_COMPANION_PLAN.md) and
[typed HTTP contract](../Agents/Companion/Frontend%20API.md#foundry-context-snapshots).

## Shreckworlds bookshelf

Opening **Shreckworlds** fetches the authenticated library directory and selects
only entries whose non-null `shrecknet_world_id` equals the configured Atlas
World ID. Every linked library appears as a section in the same bookshelf;
there is no general knowledge-World picker. Unlinked libraries and libraries for
other Worlds do not appear and their library items are not requested. Search
filters displayed book titles and authors locally. Empty shelves explain how
an administrator can link a ShreckWorld to the Atlas World.

This is UI scoping, not a new service ACL: ShreckWorld still enforces its existing
authenticated policy. No source records or embeddings are changed.

Book covers and titles open the web reader in a new tab using
`/home/shreckworld/shreckworld/shreckworld?collectionId={world_id}&sourceId={item_id}`.
Links contain no bearer token; the website uses its own signed-in session. Covers
are fetched through the player's authorized API session and fall back to book
artwork on failure. Closing or leaving the shelf releases its protected image
URLs. Late list results after sign-out or a World change do not replace the view.
The older rules-query client remains internal for compatibility with tests and
existing callers, but has no rendered question panel.

### Connection and deployment

Browser origins, methods, headers, credentials, and preflight cache duration come
only from the shared root `.env`. Follow the [shared browser CORS guide](BROWSER_CORS.md)
to rebuild/recreate the APIs for the first rollout and apply subsequent edits.

A browser network failure identifies the configured API base and suggests checking
reachability and CORS. Confirm the configured URL is browser-reachable; internal
Docker hostnames are not suitable. The production `/shreckworld-api/health`
endpoint can be reachable while cross-origin authenticated requests are blocked.
The localhost-only legacy CORS policy blocks Foundry even when service health is
normal. Updating the Foundry module alone does not update that backend policy.

The default-World setup change requires the additive migration described in the
[Foundry API guide](../API/FOUNDRY_INTEGRATION_API.md#default-world); deploy the API
before the matching ShreckOS administration update. Existing plugin setup remains
compatible. Shrecknet automatically
removes legacy CORS config rows; `/config` no longer accepts them. Deploy the
shared file policy in both API images and the module files together. Rollback restores the prior API image and module
ZIP; source files and indexed content are preserved. `knowledge-companion.js`
owns turn presentation and web-reader links; the shell owns per-world UI state.

## Release verification

Before tagging, test against a clean Foundry V14 installation:

- the module appears under **Manage Modules**;
- the module can be enabled in an empty Foundry World;
- Atlas, Shreckworlds, Chronicles, and Companion appear in the left menu;
  Narrative Scenes remain reachable from Atlas and content scrolls independently;
- Companion character selection, saved chat tabs, rename/delete confirmation,
  quota handling, history pagination, cancellation, and running-reply recovery work
  with two separate player accounts;
- Atlas directory icons are fully visible, with complete type and ontology names;
  long labels wrap, and page/related-record cards remain readable;
- keyboard focus is visible and the workspace remains usable in a narrow,
  resizable Foundry window;
- the GM can complete URL/key → World → Test all connections → Save at the default window size; long settings and results scroll while the action footer stays visible;
- failed/timeout/CORS checks report the affected service without saving; editing tested settings requires another test;
- closing or cancelling setup drops the transient key and ignores late results;
- the GM can configure reachable ShreckWorld and ShreckRPG API bases, and service requests use each player's own session;
- an individual player can sign in and browse authorized data;
- no Elder or rules-question entry points appear;
- Shreckworlds displays only libraries linked to the configured World, handles multiple/empty libraries, and books open externally;
- Chronicle capabilities control available actions, share recipients persist, revision conflicts are reported, and protected images are copied to Foundry storage before reveal;
- a browser request succeeds from `https://foundry.shrecknet.club`;
- the release ZIP contains `module.json`, `scripts/main.js`, templates, styles,
  and themes at its root.

## Compatibility

The module's manifest enforces Foundry VTT 14 as its minimum version. Update
the `verified` version only after smoke testing the applicable Foundry release.


### Evidence-first Companion queries

With backend `companion_query_pipeline_version="evidence_v2"`, an explicit table
ontology skips model inference and a saved chat ontology persists across turns.
A conflicting conversational switch asks before overriding the table scope; the
active ontology is shown and **Use page/Foundry context for the next message**
releases that override. A World containing multiple ontologies still needs a
selection. The table binding itself is never edited by conversation.

Polling reads detailed stages and durable commentary from turn snapshots, merges
messages by ID, and refreshes changed chat scope. Commentary remains in history
after completion or failure. General-knowledge fallback is a separate, one-message
opt-in exposed only when permitted by the backend; ordinary web permission is
unchanged. Factual drafts remain private during evidence verification.
Deploy the SQL migration and matching backend/workers with the clients; v2
defaults on for new/missing configuration, while explicit saved selections persist. Old
servers retain text/context chat without the additive behavior. See the
[canonical pipeline](../Agents/Companion/Companion.md#evidence-first-query-pipeline)
and [HTTP/SSE contract](../Agents/Companion/Frontend%20API.md#evidence-v2).

## Companion rich replies

The Companion workspace and compact sidecar render the same optional v1 rich
response as ShreckOS: narrative, entity cards, images/galleries, structured tables
and book excerpts. Text remains readable on older servers or unknown versions.
Source controls use admitted message evidence/targets; table sorting keeps cells
and citations together. Expansion opens an inert image dialog, never a macro or
new generation request. Content remains in the current user's private Companion
history and is not shared into Foundry chat.

Protected Atlas/ShreckWorld artwork loads with the signed-in player's service
session through approved asset paths and temporary object URLs. Foundry context
v2 supplies only `image_available`; the module resolves the admitted document UUID
in its original table and checks current OBSERVER visibility and document revision
before reading its own portrait/image. External Foundry artwork is not auto-loaded.
Unchanged version-one extraction/retry behavior remains available to old servers.
A source from another Foundry table is unavailable locally. Atlas/book/Chronicle
source actions open the corresponding authenticated website reader; web targets
open the original source page. External web pictures require an explicit load
click and never receive the player's Shrecknet bearer token.

Deploy the updated ShreckWorld, API/workers and module before enabling
`companion_rich_response_enabled` on evidence v2. Output media support is separate
from user image uploads, which this Foundry view still does not implement. No
additional Companion database migration is introduced; v2's durable-commentary
migration remains required. Disabling rich composition preserves saved text and
rich history. Recheck real v14 GM/player sheets, source CORS and source-file access
in the deployment; fixture tests are not live Foundry acceptance.
