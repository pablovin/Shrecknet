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
2. A Foundry GM opens **Game Settings → Configure Settings → Shrecknet Connection**, enters the Shrecknet API base URL and setup key, and validates it. The verified state lists permitted Worlds in the same panel; the GM selects one and configures the browser-reachable ShreckWorld and ShreckRPG API base URLs before choosing **Enable connection**. On the production proxy, the Shrecknet API base URL is `https://shrecknet.club/backend_api`; service defaults use that host at `/shreckworld-api` and `/api/shreckrpg`.
3. The enabled connection is checked immediately and every five minutes while a GM is connected. The panel records the shared online/offline status and permits a manual check.
4. The module never persists the setup key in a Foundry World setting. After the binding is saved, it does not expose another key-entry form.
5. Each player opens the **Shrecknet Worlds** globe control, signs in with their own Shrecknet account, and remains signed in on that browser until they use **Sign out**, the token expires, or it is revoked. ShreckWorld and ShreckRPG requests use that player's bearer token; do not configure a shared service token in Foundry.
6. A GM can update the ShreckWorld and ShreckRPG API bases later in the same settings panel. URLs must use HTTPS outside localhost and cannot contain credentials, query parameters, or fragments.

Close and reopen the Shrecknet workspace after changing service URLs so its
client reads the updated world settings.

Chronicle access and sharing follow ShreckRPG authorization and capability
responses. Saves include the expected revision; on a conflict, reload before
editing again. Chronicle `game_id` is not associated with a Foundry World.
Protected Chronicle image assets are copied to Foundry-managed storage before
being shown to players or posted to chat.

The **Elder** workspace lists Elders assigned to the configured Atlas
ontologies; opening it from a page or Narrative Scene provides that resource as
optional context. **ShreckWorld** provides a bookshelf and grounded rules companion with cited
answers and source excerpts. ShreckWorld source visibility
is determined by that service; Foundry adds no per-world ACL.

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

The current module release is **0.3.3**; see the
[release notes](../../changelogs/foundry-v0.3.3.md). The release workflow uses the
matching `changelogs/foundry-vX.Y.Z.md` as its GitHub Release notes.

To build an installable archive locally from the repository root:

```bash
node --test integrations/foundry/shrecknet-foundry/tests/*.test.mjs
mkdir -p dist/foundry-v0.3.3
(cd integrations/foundry/shrecknet-foundry && zip -qr ../../../dist/foundry-v0.3.3/shrecknet-foundry-v0.3.3.zip . -x '.DS_Store' -x '__MACOSX/*')
cp integrations/foundry/shrecknet-foundry/module.json dist/foundry-v0.3.3/module.json
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
2. Commit and merge that change.
3. Create and push the `foundry-vX.Y.Z` tag on that commit.
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

## Elder replies and interrupted connections

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
is visible. Only saved messages are displayed as replies; provisional drafts are
not presented as final output. Reopening or refreshing a running chat resumes its
`active_turn_id` without resending a question. **Stop reply** cancels the saved
turn. Turn failures are reported and leave history available. A transport failure
pauses polling; **Refresh** checks saved state before further sending. Changing
chat, character, or chat metadata is disabled during a running reply. Navigation
away pauses polling; returning resumes it. Closing or signing out clears local
Companion state and fences late responses without cancelling server work.

`character-companion.js` owns this workspace's state and actions; the shared API
client owns HTTP and authentication. This Foundry view currently supports text
messages; image uploads, private attachment previews, rapport controls, and live
SSE deltas remain available through other Companion clients. Backend Companion
feature/model configuration must already be enabled. No endpoint or database
migration is introduced. Rollback of the module preserves backend chats.

## Elder Companion

The Elder workspace displays its portrait (or fallback emblem), name, description,
and switcher above an active-context strip. The conversation scrolls while the
identity, context, and composer remain accessible. User messages appear as
right-aligned bubbles; responses use the Elder's name. Draft questions survive
rerenders and failures, clear after a successful reply, and clear when switching
Elders or signing out.

Opening Elder from an Atlas page or Narrative Scene captures the page identifier
and label. **Clear page focus** removes `instance_id` from subsequent queries
without deleting history. Page focus guides retrieval; it is not an exclusive
source restriction. Expandable sources display returned `evidence_chunks` text,
falling back to `canonical_text` or context text. **Open Atlas page** requires an
explicit parent-page identifier; graph node IDs are never treated as page IDs.
Answers and excerpts render as escaped text.

History still loads the user's most recent Elder chat, without a per-World
partition. Floating/sidecar modes, claim-level links, Actor mappings, Chronicle
excerpt saving, and generated answer language/length changes remain future work.
No API, SDK, database migration, or configuration change is required. Existing
module rollback instructions apply. `elder-conversation.js` owns source
presentation and transport recovery; the shell owns UI state.

## ShreckWorld bookshelf and rules companion

Opening **ShreckWorld** lists the signed-in user's accessible knowledge worlds.
Opening a world displays **Your Books** beside **Shreck Companion**. The shelf
can be resized horizontally; below 650px of content width, **Show Books** and
**Show Companion** switch panes. The conversation scrolls independently of the
composer. Search filters book titles locally.

Book covers and titles open the web reader in a new browser tab using
`/home/shreckworld/shreckworld/shreckworld?collectionId={world_id}&sourceId={item_id}`.
**Manage library** opens the same route with only `collectionId`. These links
carry no bearer token; the website uses its own signed-in session. Foundry does
not embed original files. Covers are fetched through the authenticated service
API and fall back to an emblem when unavailable.

**Use for questions** controls source selection independently from opening books.
No selected books means all sources in the current knowledge world. Multiple
selections send `library_item_ids`; questions request `mode="answer"`,
`retrieval_strategy="planned"`, and `response_detail="concise"`. The existing
single-source client contract remains supported. Each question is independent;
previous turns are displayed but are not sent as conversational model context.
Drafts, selected books, and completed turns are retained per knowledge world
within this workspace instance. Signing out clears them; closing the window or
reloading Foundry does not persist them. Failed questions retain their draft,
duplicate pending submissions are ignored, and late answers after sign-out are
discarded.

Answers display backend support status, source warnings, missing information,
and conflicts without converting unavailable evidence into a supported answer.
Expandable citations show the supplied authority tier, excerpt, physical page,
and printed page label when available. Source links open the correct book;
page-specific navigation and highlighting require a confirmed web reader anchor
contract and are not currently implemented. System and edition fields are not
invented when absent from the source API. See the
[reader API](../API/SHRECKWORLD_READER_API.md) for synthesis and citation guarantees.

**Copy answer** copies the explanation and source excerpts. **Send to Foundry
chat** explicitly shares that answer and citations using the user's normal
speaker and default chat visibility, subject to Foundry message permission. Text
is escaped before posting; tokens and protected cover URLs are never included.
The sender must choose material appropriate for the chat audience.

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

No schema migration or SDK model change is required. Shrecknet automatically
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
- the GM can configure a Shrecknet URL, setup key, and World from Game Settings and see an online status;
- the GM can configure reachable ShreckWorld and ShreckRPG API bases, and service requests use each player's own session;
- an individual player can sign in and browse authorized data;
- Atlas Elders and history load, and an opened Atlas record can be used as optional Elder context;
- ShreckWorld books open externally, selection scopes grounded answers, source excerpts and warnings render, compact pane switching preserves drafts, and copy/chat actions share only the chosen turn;
- Chronicle capabilities control available actions, share recipients persist, revision conflicts are reported, and protected images are copied to Foundry storage before reveal;
- a browser request succeeds from `https://foundry.shrecknet.club`;
- the release ZIP contains `module.json`, `scripts/main.js`, templates, styles,
  and themes at its root.

## Compatibility

The module's manifest enforces Foundry VTT 14 as its minimum version. Update
the `verified` version only after smoke testing the applicable Foundry release.
