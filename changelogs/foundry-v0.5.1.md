# Shrecknet Foundry 0.5.1

- Match the Shrecknet launcher to native Foundry control dimensions, with a white
  background and a purple Shrecknet mark. Preserve native borders and interaction
  states, and remove the oversized black-logo override.
- Render Companion headings, lists, tables, code and basic inline Markdown in the
  workspace and compact chat, including rich-response narrative passages and the
  plain-text fallback. Raw HTML and arbitrary model links/images remain inert.

Requires Foundry VTT **14**. Update through the stable manifest and reload the table:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

Pinned manifest:

```text
https://github.com/pablovin/Shrecknet/releases/download/foundry-v0.5.1/module.json
```

The archive is `shrecknet-foundry-v0.5.1.zip`, with `module.json` at its root.
No API contract, configuration or database migration changes are introduced by
this module update. Existing 0.5.0 backend compatibility requirements remain.
Restore the previous module archive to roll back; saved conversations are retained.

Verification: all 96 module tests passed with `node --test
integrations/foundry/shrecknet-foundry/tests/*.test.mjs`. Live Foundry v14 visual
acceptance remains unverified.

See the [Foundry deployment guide](../Documentation/Deployment/FOUNDRY_MODULE.md).
