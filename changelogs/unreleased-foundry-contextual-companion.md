# Unreleased — contextual Foundry Companion chat

- Add a compact/full Companion layout sharing one workspace, saved conversations,
  authentication and polling lifecycle.
- Include visible removable table background and the selected Shrecknet World;
  expose explicit World lore and linked rules-library selection.
- Attach Actor, Item and current Journal page context through header actions,
  document menus, drag/drop and UUID links. Quick questions prepare a draft.
- Extract permitted visible text and readonly labeled values independently of the
  game system; exclude raw system data, hidden content and unrevealed secrets.
- Add bounded `foundry_context` to existing Companion turns and SDK `send`, with
  snapshot provenance, source navigation and immutable contextual retries.
- Preserve per-chat drafts/references while the window is closed; clear them on
  sign-out or connection change. No SQL migration or writes to Foundry documents.
- Deploy backend/Companion workers before the module. Text chat remains available
  against an older server. Live Foundry v14/custom sheet and provider acceptance
  remain required before publishing a release. Combat/map context is deferred.

See the [canonical Foundry contract](../Documentation/Deployment/FOUNDRY_MODULE.md#contextual-chat-and-compact-sidecar)
and [Companion snapshot API](../Documentation/Agents/Companion/Frontend%20API.md#foundry-context-snapshots).
