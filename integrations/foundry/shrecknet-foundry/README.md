# Shrecknet Foundry Module

Read-only Shrecknet World browsing for Foundry VTT 14 and later. This directory
is self-contained so it can become its own repository without imports from the
Shrecknet backend repository.

## Connect a World

1. Create an integration key on Shrecknet with `POST /config/integrations/foundry`.
2. As a Foundry GM, open **Game Settings → Configure Settings → Shrecknet Connection**.
3. Enter the Shrecknet URL and key, test the connection, choose a Shrecknet World, and select **Enable connection**. The panel shows its online/offline status and lets a GM check it again.
4. Each Foundry player opens the Shrecknet-logo control and signs in with their own Shrecknet account. Their sign-in is remembered for that browser session; use **Sign out** in the Shrecknet window to end it.

The setup key is deliberately never written to a Foundry setting. It is used
only to validate the server and list Worlds during the GM setup action. User
tokens are held in browser session storage and are never shared with other
Foundry users.

## Architecture

- `scripts/api/` is the only HTTP client and calls existing Shrecknet routes.
- `scripts/apps/` owns Foundry UI state.
- `scripts/navigation/` owns Back/Forward/Home state.
- `templates/` owns markup; `themes/` and `styles/` own visual presentation.

The only new Shrecknet routes are the Foundry setup-key endpoints. Content routes
are intentionally reused rather than mirrored.

## v0.1 limits

The module is read-only. It does not create Foundry Actors, Items, Journals, or
Canvas Scenes, does not sync content, and does not implement Companion or
Character Agent interaction. "Narrative Scene" always means a Shrecknet
narrative resource, not a Foundry Canvas Scene.
