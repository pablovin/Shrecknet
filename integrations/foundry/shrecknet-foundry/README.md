# Shrecknet Foundry Module

Shrecknet Atlas, Shreckworlds, Chronicles, and Character Companion workspaces for Foundry VTT 14
and later. The resizable ShreckOS-style workspace keeps its navigation available
while browsing. This directory is self-contained so it can become its own
repository without imports from the Shrecknet backend repository.

## Connect a World

1. Create an integration key on Shrecknet with `POST /config/integrations/foundry`.
2. As a Foundry GM, open **Game Settings → Configure Settings → Shrecknet Connection**.
3. Enter the Shrecknet API URL (production: `https://shrecknet.club/backend_api`) and setup key, then choose **Continue · choose World →**. Select a World; the administrator default is preselected when set. Service URLs are filled automatically and can be changed under **Advanced · service URLs**. Choose **Test all connections**, review the results, then **Save connection**. The scrollable window keeps its footer controls reachable at the default size.
4. The enabled connection is checked immediately and every five minutes while a GM is connected; the panel shows its shared online/offline status and permits a manual check.
5. Each Foundry player opens **Shrecknet Worlds** using the Worlds-globe control and signs in with their own Shrecknet account. Their sign-in persists on that browser until they use **Sign out**, the token expires, or it is revoked.
The service URL defaults use the Shrecknet API host at `/shreckworld-api` and
`/api/shreckrpg`; a GM can change them later.

After changing service URLs, open the Shrecknet workspace again so its client uses
the updated world settings.

Use **Update World connection** in GM settings to re-enter the setup key and
apply a changed admin binding; existing settings remain until its replacement is
saved. **Keep saved connection** cancels reconfiguration. After editing service
URLs, run **Test all connections** before **Save changes**.

The setup key is deliberately never written to a Foundry setting. It is used
only to validate the server and list Worlds during GM setup and final verification.
It stays in window memory until Save, Back, cancel, or close. User
tokens are held in browser-local persistent storage and are never shared with
other Foundry users. A player remains signed in after closing or reloading the
Foundry table until they choose **Sign out**, their token expires, or it is
revoked.

ShreckWorld and ShreckRPG calls use the signed-in player's Shrecknet bearer
token. The module does not store or use a shared service token. Configure both
services to allow the Foundry browser origin and the necessary authorization,
content-type, GET, POST, PUT, DELETE, and OPTIONS requests.

**Test all connections** checks the setup key/selected World and all three API
health endpoints, with separate results and ten-second HTTP timeouts. A failed
check or edited setting prevents saving. These are browser reachability/liveness
checks; each player still signs in to authorize their private content.

## Architecture

- `scripts/api/` is the only HTTP client and calls existing Shrecknet routes.
- `scripts/apps/` owns Foundry UI state; `shrecknet-connection-settings.js` owns
  the GM wizard independently of the content workspace.
- `scripts/navigation/` owns Back/Forward/Home state.
- `templates/` owns markup; `themes/` and `styles/` own visual presentation.
- The workspace provides persistent Atlas, Shreckworlds, Chronicles, and Companion navigation,
  search, browser history, and an independently scrollable content area. Its
  layout adapts to narrow Foundry windows and uses system UI typography,
  restrained ShreckOS colors, and visible keyboard focus.

The only new Shrecknet routes are the Foundry setup-key endpoints. Content routes
are intentionally reused rather than mirrored.

The World-browser search searches page names across every ontology in the
configured World. Results are loaded as complete pages, so opening a result does
not require a second content request.

The Atlas Explorer centers reading between collapsible relationship/scene and
properties panels, with a focus-reading option and layouts that follow the
resizable workspace width. **Show in chat** shares the page name, image, each
section's selected Original or Generated text, and relationships grouped by type
with target page names and entity types. Related pages' body text is not shared.
This is the Atlas page's only content-sharing action. GM image reveal remains
available in other module views. See the [canonical Foundry documentation](https://github.com/pablovin/Shrecknet/blob/main/Documentation/Deployment/FOUNDRY_MODULE.md#atlas-explorer)
for source-selection, chat visibility, metadata caching, and compatibility rules.

Folders, search results, and related records render as image cards that open the
underlying page. The browser displays a loading indicator while it fetches
content, including related-record resolution.

The navigation search searches both pages and Narrative Scene names in the
selected World. Its results show page cards first and matching scenes grouped by
source page beneath them. Pressing Enter in either search field runs the same
in-window search as its button and never navigates the Foundry browser URL.

The World index uses each displayed entity type's `image_url`; when that is not
set, it falls back to the owning ontology's `image_url`.

Narrative Scenes are grouped beneath collapsed source-page panels. Opening a
panel reveals that source page's scenes, while long scene and document content
scrolls within the Foundry window.

Scene details present their source, related records, and milestone relations as
image cards. When a scene has an adjacent scene in its source-page ordering, the
detail view provides direct Previous scene and Next scene links.

## Connected workspaces

- **Companion** opens private Character Companion conversations with a portrait
  hero card and saved chat tabs. Choose a character, create or switch chats,
  rename them, delete with confirmation, and load earlier messages. Running
  replies can be stopped or recovered by reopening the chat. The server enforces
  the account-wide ten-chat limit. This view supports contextual text conversations; see the
  [canonical Foundry guide](https://github.com/pablovin/Shrecknet/blob/main/Documentation/Deployment/FOUNDRY_MODULE.md#character-companion)
  for selection, polling, authorization, CORS, and compatibility details.
  Narrative Scenes remain accessible through Atlas home. **Change companion** and
  **Conversation options** expand selection and management controls; source excerpts
  are available beneath saved replies.

- **Shreckworlds** opens a bookshelf containing only libraries whose
  `shrecknet_world_id` matches the configured Atlas World. All matching libraries
  appear together; search filters titles/authors, covers use player authorization,
  and book links open the web reader without credentials in the URL. No linked
  libraries produces an explanatory empty state. Elder and rules-question panels
  have no visible entry point in the four-menu workspace.
- **Chronicles** supports reading, creating, editing, sharing, and deleting
  documents according to the signed-in user's ShreckRPG permissions. Sharing
  controls appear only when the backend grants the `share` capability. Saves use
  `revision` for optimistic conflict detection. Chronicle `game_id` is not
  mapped to Foundry.
- Chronicle images are fetched with the authorized player session. A GM reveal
  or chat share uploads protected images to Foundry-managed storage first; a
  protected ShreckRPG asset URL or bearer token is never broadcast through the
  Foundry socket.

## API routes used

All routes below use the base URL configured for their service. The ShreckWorld
base defaults to `/shreckworld-api`; the ShreckRPG base defaults to
`/api/shreckrpg`.

- Retained internal Atlas Elder client (no visible menu action): `GET /ontologies/{ontology_id}/elders`,
  `GET /jobs/elder/chats/?agent_id={agent_id}&limit=100&offset=0`,
  `GET /jobs/elder/chats/{chat_id}?include_history=true`,
  `POST /jobs/elder/chats/`, and `POST /jobs/elder/{agent_id}/query` with
  `query`, `mode`, `include_trace`, `chat_id`, and the optional contextual
  `instance_id`.
- ShreckWorld: `GET /shreckworlds`,
  `GET /shreckworlds/{shreckworld_id}/library-items`,
  `GET /shreckworlds/{shreckworld_id}/library-items/{library_item_id}/cover`,
  and, in the retained internal rules client, `POST /shreckworlds/{shreckworld_id}/query` with the optional
  `library_item_ids` selection (or legacy `library_item_id`), `mode="answer"`,
  `retrieval_strategy="planned"`, and `response_detail="concise"`.
- Chronicles: `GET` and `POST /chronicles`,
  `GET`, `PUT`, and `DELETE /chronicles/{chronicle_id}`,
  `GET /chronicles/recipients`,
  `POST /chronicles/{chronicle_id}/assets`, and the protected asset `GET`
  using the URL returned by the upload route. Chronicle updates send
  `expected_revision` and, when permitted, `share_user_ids`.

## Contextual Companion

The **Ask Companion** scene-control launcher opens compact chat. The workspace's
**Compact chat**/**Expand** controls switch the same conversation between layouts.
Actor, Item and current Journal page header actions attach the document without
sending. Drag/drop, UUID links and an explicit assigned-character action use the
same permission-filtered extractor. Table title/description, system metadata and
the connected Shrecknet World appear as removable context. Quick questions fill
the composer; Send remains explicit.

Expand **Review attached context** to preview content and choose World lore and a
linked rules library. Attachments refresh on each new Send and remain per chat in
memory. Ambiguous retries reuse the original snapshot. Partial generic extraction
uses visible text/readonly labeled fields; closed or unsupported sheets provide
metadata only. Raw system JSON, hidden content and unrevealed secrets are excluded.
Foundry source controls reopen permitted documents in their original table.

Deploy the updated backend and Companion workers before the module; old servers
retain text chat with a notice. No new database columns are needed. See the
[canonical contextual chat contract](https://github.com/pablovin/Shrecknet/blob/main/Documentation/Deployment/FOUNDRY_MODULE.md#contextual-chat-and-compact-sidecar)
for limits, trust boundaries, recovery, and remaining live Foundry checks.

## Current limits

The module does not create Foundry Actors, Items, Journals, or Canvas Scenes and
does not synchronize Atlas data. ShreckWorld visibility follows the service's
authenticated API policy; Foundry does not add a per-world access-control layer.
Companion image uploads, private attachment previews, and rapport controls are not implemented in this view. "Narrative
Scene" always means a Shrecknet narrative resource, not a Foundry Canvas Scene.

## Module checks

From this directory, run `node --test tests/*.test.mjs` to exercise the
service URL, authorization, Character Companion admission/recovery and chat management, Elder pagination, ShreckWorld query, and Chronicle
revision/asset request contracts, Foundry snapshot isolation/retry/navigation, plus manifest download and packaged-file checks.
