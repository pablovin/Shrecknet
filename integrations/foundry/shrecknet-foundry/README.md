# Shrecknet Foundry Module

Read-only Shrecknet World browsing for Foundry VTT 14 and later. This directory
is self-contained so it can become its own repository without imports from the
Shrecknet backend repository.

## Connect a World

1. Create an integration key on Shrecknet with `POST /config/integrations/foundry`.
2. As a Foundry GM, open **Game Settings → Configure Settings → Shrecknet Connection**.
3. Enter the Shrecknet API base URL (for the production proxy, `https://shrecknet.club/backend_api`) and key, then test the connection. The verified state presents the permitted Shrecknet Worlds directly in this panel. Select one and choose **Enable connection**.
4. The enabled connection is checked immediately and every five minutes while a GM is connected; the panel shows its shared online/offline status and permits a manual check.
5. Each Foundry player opens **Shrecknet Worlds** using the Worlds-globe control and signs in with their own Shrecknet account. Their sign-in persists on that browser until they use **Sign out**, the token expires, or it is revoked.

The setup key is deliberately never written to a Foundry setting. It is used
only to validate the server and list Worlds during the GM setup action. User
tokens are held in browser-local persistent storage and are never shared with
other Foundry users. A player remains signed in after closing or reloading the
Foundry table until they choose **Sign out**, their token expires, or it is
revoked.

## Architecture

- `scripts/api/` is the only HTTP client and calls existing Shrecknet routes.
- `scripts/apps/` owns Foundry UI state.
- `scripts/navigation/` owns Back/Forward/Home state.
- `templates/` owns markup; `themes/` and `styles/` own visual presentation.

The only new Shrecknet routes are the Foundry setup-key endpoints. Content routes
are intentionally reused rather than mirrored.

The World-browser search searches page names across every ontology in the
configured World. Results are loaded as complete pages, so opening a result does
not require a second content request.

Readers can post the currently selected Text or Generated text source to Foundry
chat with its page name and image. A GM can also choose **Show players** on a
Shrecknet image to open it for every connected player.

Folders, search results, and related records render as image cards that open the
underlying page. The browser displays a loading indicator while it fetches
content, including related-record resolution.

The World index uses each displayed entity type's `image_url`; when that is not
set, it falls back to the owning ontology's `image_url`.

## v0.1 limits

The module is read-only. It does not create Foundry Actors, Items, Journals, or
Canvas Scenes, does not sync content, and does not implement Companion or
Character Agent interaction. "Narrative Scene" always means a Shrecknet
narrative resource, not a Foundry Canvas Scene.
