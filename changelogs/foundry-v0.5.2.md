# Shrecknet Foundry 0.5.2

- Give Companion conversations more room with a single portrait/name header,
  a Change companion button, and a full-height message area in both layouts.
- Move saved conversations and New chat into a small bottom bar. Its overflow
  menu keeps Refresh, Sign out, Rename and Delete available.
- Show removable document-type attachment icons inside the composer area. Hover
  or focus identifies their name, type and source table; clicking opens previews.
  Attach / context contains document links, table background, lore/rules choices
  and quick questions. Table background remains included by default.
- Add Ask Companion directly beside supported Actor, Item and Journal page window
  controls. Opening it attaches context without sending and checks permissions
  again on click. Directory context menu actions remain available.

Requires Foundry VTT **14**. Update through the stable manifest and reload the table:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

Pinned manifest:

```text
https://github.com/pablovin/Shrecknet/releases/download/foundry-v0.5.2/module.json
```

The archive is `shrecknet-foundry-v0.5.2.zip`, with `module.json` at its root.
No API contract, configuration or database migration changes. Existing 0.5.0
backend compatibility requirements remain. Roll back using the previous published
module archive; saved conversations are retained.

Verification: 98 module tests passed. Chromium template/style checks at 420, 680
and 960 pixels confirmed a scrollable message area, reachable footer and composer,
no horizontal overflow and no nested forms. Live Foundry v14 visual acceptance
and system-specific sheet title bars remain unverified.

See the [Foundry deployment guide](../Documentation/Deployment/FOUNDRY_MODULE.md).
