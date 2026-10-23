# Foundry v0.3.3

## Added

- Character Companion workspace with a portrait hero card, character selection,
  saved conversation tabs, and a full-height private chat.
- Create, rename, and delete saved chats with deletion confirmation, optimistic
  revision checks, account-wide chat usage, and earlier-message pagination.
- Durable reply admission, cancellation, and recovery of running replies when
  reopening a conversation. Ambiguous retries retain their idempotency keys.
- Responsive Companion layouts, safe text rendering, and local state cleanup
  on sign-out or close, including protection against late private responses.

## Changed

- The left menu contains Atlas, Shreckworlds, Chronicles, and Companion, in that
  order. Narrative Scenes and Atlas Elders remain accessible through Atlas.
- Companion uses the existing Character Companion API independently of Atlas
  Elders and the ShreckWorld rules companion.

## Deployment and compatibility

The module version is `0.3.3`; Foundry VTT 14 remains the minimum version.
No database migration or public API change is required. The backend Character
Companion feature and its models must already be enabled. Browser CORS must
permit the `Idempotency-Key` header and the required mutation methods.
This view supports text chat; image uploads and rapport controls are not included.

The installable archive is `shrecknet-foundry-v0.3.3.zip`, with `module.json`
at its root. Publish the immutable `foundry-v0.3.3` tag through the existing
release workflow to attach that ZIP and its matching manifest to GitHub Releases.
A local ZIP can also be extracted into Foundry's `Data/modules/shrecknet-foundry/`
directory. Reload Foundry after updating. Roll back by restoring the previous
module ZIP; server-persisted chats remain available.

See the [Foundry deployment guide](../Documentation/Deployment/FOUNDRY_MODULE.md#character-companion)
for the full feature contract and operational requirements.
