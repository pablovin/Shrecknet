# Shrecknet Foundry 0.5.0

## Rich Companion replies

- Render the optional v1 rich response in both the full workspace and the compact
  sidecar: narrative passages, entity cards, images and galleries, structured
  property tables and book excerpts. Older servers or unknown envelopes keep the
  saved plain text.
- Source controls use admitted message evidence/targets. Table sorting keeps cells
  and their citations together, and image expansion opens an inert dialog rather
  than a macro or new generation request.
- Protected Atlas/ShreckWorld artwork loads with the signed-in player's service
  session; Foundry-local portraits recheck current document visibility and
  revision before display. External web images load only on an explicit click and
  never receive the player's bearer token.
- Show rules-index warnings on the answer when sources require reindexing for the
  active embedding model or have no ready index, including searched/requested
  source counts.

## Companion controls and progress

- Always offer web search on Companion turns when the backend advertises a
  configured provider. There is no per-turn web toggle; the backend routing layer
  decides whether a turn actually needs the web tool.
- Add a labeled, unverified **general knowledge** opt-in when the backend permits
  it, and a **Use page/Foundry context for the next message** control when a chat
  holds a conversational World override.
- Map `pipeline.status` stages to readable progress labels (choosing the world,
  planning the search, checking sources, comparing evidence, preparing the reply,
  checking factual claims, checking citations, and so on), merge streamed
  commentary into the log, and refresh the active ontology when it changes.
- Negotiate Foundry context version 2 with the backend and supply only
  `image_available` for attached documents; version-one extraction and retry
  behavior remains available to older servers.

## Install and update

Requires Foundry VTT **14**. Install from Foundry Setup → Add-on Modules → Install
Module using the stable manifest:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

Pinned manifest for this release:

```text
https://github.com/pablovin/Shrecknet/releases/download/foundry-v0.5.0/module.json
```

The archive is `shrecknet-foundry-v0.5.0.zip` and contains `module.json` directly
at its root. Reload the Foundry table after updating. Existing installations can
check for updates through their stable manifest URL. Restore the previous module
archive to roll back; backend chat history remains available.

## Backend compatibility and verification

Deploy the matching Shrecknet API, Companion workers and ShreckWorld together
before (or with) the module. Rich replies require an evidence-v2 backend with
`companion_rich_response_enabled` (default true for new/missing settings); explicit
false values remain respected. Foundry context v2 requires a backend that accepts
context version 2 — older servers keep text chat and client-provided snapshots
without the image availability hint. No additional Companion database migration is
introduced beyond the evidence-v2 durable-commentary migration already required by
0.4.0. Existing keys, bindings and ordinary text-turn idempotency replays remain
valid.

Verification: 93 module tests passed via `node --test
integrations/foundry/shrecknet-foundry/tests/*.test.mjs`, including manifest
download/asset checks and rich-response rendering/fallback fixtures. Real Foundry
v14 GM/player sheets, custom-sheet acceptance, source CORS/file access and live
provider answer quality remain unverified in a real table.

- [Foundry behavior, limits and deployment](../Documentation/Deployment/FOUNDRY_MODULE.md)
- [Companion rich replies](../Documentation/Deployment/FOUNDRY_MODULE.md#companion-rich-replies)
- [Snapshot API](../Documentation/Agents/Companion/Frontend%20API.md#foundry-context-snapshots)
