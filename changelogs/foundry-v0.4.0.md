# Shrecknet Foundry 0.4.0

## Contextual Companion chat

- Open a compact, resizable Companion sidecar from the Foundry scene controls.
  Expand it into the full workspace while retaining the same saved conversation,
  draft, private account and reply recovery.
- Attach table title/description, installed system metadata and the connected
  Shrecknet World as visible, removable context. Choose a World lore collection
  and a linked rules library explicitly.
- Use **Ask Companion** on supported Actor, Item and current Journal page sheets,
  document context menus, drag/drop or UUID links. Attaching never sends a message;
  quick questions prepare a draft for review.
- Extract readable content independently of the game system. Exclude raw system
  JSON, hidden fields, unrevealed secrets and editable values. Unsupported or
  closed sheets provide metadata with partial coverage.
- Save snapshot provenance and provide source controls that recheck the current
  table and document permissions before opening a sheet.
- Refresh context for new questions while preserving the exact snapshot and
  idempotency key on ambiguous retries. Preserve drafts and context references
  while closed; clear account data on sign-out, expiry or account/connection change.

## Workspace and setup

- Keep Atlas, Shreckworlds, Chronicles and Companion as the four workspace menus,
  with a responsive icon rail and quieter conversation controls.
- Show only ShreckWorld libraries linked to the configured Atlas World. Chronicles
  and Companion retain the signed-in player's existing service permissions.
- Guide GM setup through URL/key, World selection, separate service checks and
  explicit Save, with reachable footer controls and retained custom service URLs.
- Support the administrator's default World, setup preselection and explicit GM
  rebinding without replacing a saved connection before successful verification.

## Install and update

Requires Foundry VTT **14**. Install from Foundry Setup → Add-on Modules → Install
Module using the stable manifest:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

Pinned manifest for this release:

```text
https://github.com/pablovin/Shrecknet/releases/download/foundry-v0.4.0/module.json
```

The archive is `shrecknet-foundry-v0.4.0.zip` and contains `module.json` directly
at its root. Reload the Foundry table after updating. Existing installations can
check for updates through their stable manifest URL. Restore the previous module
archive to roll back; backend chat history remains available.

## Backend compatibility and verification

Deploy the matching Shrecknet API and Companion workers before the module for
`foundry_context` support. Older servers retain text chat with a notice instead
of receiving unsupported context. The contextual feature uses existing JSON
columns and requires no SQL migration. The setup update's nullable integration
`default_world_id` column is added by the existing backend startup migration.
Existing keys/bindings and ordinary text-turn idempotency replays remain valid.
Keep the configured browser CORS permissions for bearer authorization,
`Idempotency-Key`, and existing mutation methods.

Verification: 84 module tests, 55 focused backend tests and 62 SDK tests passed.
Chromium and Firefox passed native DOM filtering/state fixtures and compact/full
layout checks. Real Foundry v14/custom-sheet acceptance and live provider answer
quality remain unverified. Combat/map snapshots, image uploads, automatic live
lookups and game-state actions are not included. A broader Companion test run
had two legacy fixture/configuration failures; see the validation record.

- [Foundry behavior, limits and deployment](../Documentation/Deployment/FOUNDRY_MODULE.md)
- [Snapshot API](../Documentation/Agents/Companion/Frontend%20API.md#foundry-context-snapshots)
- [Verification details](../Documentation/Agents/Companion/Validation.md#foundry-contextual-chat-verification--2026-11-06)
