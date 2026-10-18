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
`OPTIONS` methods. ShreckWorld and ShreckRPG requests also originate in the
Foundry browser; configure each service's CORS allowlist for this origin and
allow authorization/content-type headers plus `GET`, `POST`, `PUT`, `DELETE`,
and `OPTIONS`.

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
optional context. **ShreckWorld** shows source metadata and cited, extractive
query evidence rather than a synthesized answer. ShreckWorld source visibility
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

## Release verification

Before tagging, test against a clean Foundry V14 installation:

- the module appears under **Manage Modules**;
- the module can be enabled in an empty Foundry World;
- the Atlas and Narrative Scenes workspace navigation remains available while
  browsing, and each content area scrolls independently;
- keyboard focus is visible and the workspace remains usable in a narrow,
  resizable Foundry window;
- the GM can configure a Shrecknet URL, setup key, and World from Game Settings and see an online status;
- the GM can configure reachable ShreckWorld and ShreckRPG API bases, and service requests use each player's own session;
- an individual player can sign in and browse authorized data;
- Atlas Elders and history load, and an opened Atlas record can be used as optional Elder context;
- ShreckWorld source metadata, scoped evidence queries, and citations render for an authenticated user;
- Chronicle capabilities control available actions, share recipients persist, revision conflicts are reported, and protected images are copied to Foundry storage before reveal;
- a browser request succeeds from `https://foundry.shrecknet.club`;
- the release ZIP contains `module.json`, `scripts/main.js`, templates, styles,
  and themes at its root.

## Compatibility

The module's manifest enforces Foundry VTT 14 as its minimum version. Update
the `verified` version only after smoke testing the applicable Foundry release.
