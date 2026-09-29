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

The browser executes module requests from that origin, so the Shrecknet API must
allow it in `cors_allow_origins`. New deployments receive this value from
`configs/shrecknet.initial.json`. For an existing deployment, an administrator
must add the origin through the Configuration Manager without discarding any
existing allowed origins.

The existing `cors_allow_methods` and `cors_allow_headers` defaults permit the
module's `Authorization` and `X-Shrecknet-Integration-Key` headers. If those
values are tightened later, retain both headers and the `POST`, `GET`, and
`OPTIONS` methods.

## Integration setup

1. A Shrecknet administrator creates a setup key with
   `POST /config/integrations/foundry` and optionally restricts it to World IDs.
2. A Foundry GM opens **Game Settings → Configure Settings → Shrecknet Connection**, enters the Shrecknet API base URL and setup key, and validates it. The verified state lists permitted Worlds in the same panel; the GM selects one and chooses **Enable connection**. On the production proxy, the API base URL is `https://shrecknet.club/backend_api`.
3. The enabled connection is checked immediately and every five minutes while a GM is connected. The panel records the shared online/offline status and permits a manual check.
4. The module never persists the setup key in a Foundry World setting. After the binding is saved, it does not expose another key-entry form.
5. Each player opens the **Shrecknet Worlds** globe control, signs in with their own Shrecknet account, and remains signed in on that browser until they use **Sign out**, the token expires, or it is revoked.

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
   Release with `shrecknet-foundry-vX.Y.Z.zip` attached.
5. Install or update from the stable manifest URL through Foundry's **Install
   Module** dialog.

The release tag is immutable. Do not move or recreate it after publishing.

## Release verification

Before tagging, test against a clean Foundry V14 installation:

- the module appears under **Manage Modules**;
- the module can be enabled in an empty Foundry World;
- the GM can configure a Shrecknet URL, setup key, and World from Game Settings and see an online status;
- an individual player can sign in and browse authorized data;
- a browser request succeeds from `https://foundry.shrecknet.club`;
- the release ZIP contains `module.json`, `scripts/main.js`, templates, styles,
  and themes at its root.

## Compatibility

The module's manifest enforces Foundry VTT 14 as its minimum version. Update
the `verified` version only after smoke testing the applicable Foundry release.
