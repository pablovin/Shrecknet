# Foundry v0.3.2

This entry collects the current batch of Foundry plugin updates.

## Fixed

- Shared CORS dotenv mounts use `/app/browser.env`, outside the read-only
  `/configs` mount, so Docker can create the bind-mount target at startup.
  Rebuild and recreate both API containers to apply the corrected mount and loader.

- Atlas directory cards expand to show their complete icons, entity-type names,
  and owning ontology names. Long names wrap instead of being clipped by
  Foundry's fixed button height.
- Page and related-record cards also expand to keep their labels visible.

- Elder displays a returned answer immediately in the submitted conversation.
  Lost connections recover newly saved replies through bounded reads of that
  same chat, without resubmitting the question or switching conversations.
- ShreckOS Atlas receives the same direct-reply/recovery fix. Its Elder POSTs
  (including legacy administration) use a 15-minute browser deadline, and its
  bundled Nginx proxy permits 900 seconds on Elder query routes instead of the
  ordinary 15 seconds. Rebuild the frontend image and align outer proxy timeouts.

- Shrecknet and ShreckWorld share file-only CORS configuration in the root `.env`.
  Rebuild/recreate both APIs to install the shared loader and read-only file mounts.
  Legacy Shrecknet CORS database settings are removed and runtime updates rejected.
  Browser connection errors identify the configured service base.

## Added

- Atlas Explorer reading layout with a compact page header, collapsible grouped
  relationships and ordered scenes, a properties panel, and focus-reading mode.
  Layouts adapt to the resizable content width.
- One page-level **Show in chat** action shares the page name, image, each
  section's selected Original or Generated text, and relationships grouped by
  type with target page names and entity types. Empty sections remain shareable.
- Relationship navigation uses page names and type labels, deduplicates targets
  within each group, and handles unavailable targets without exposing IDs.

- ShreckWorld bookshelf and grounded rules companion with independent book
  selection, multi-source queries, external reader links, expandable citations,
  copy/chat actions, and compact Books/Companion panes. Workspace-local history
  and drafts are retained per knowledge world and cleared on sign-out.

## Changed

- Atlas pages use **Show in chat** as their sole content-sharing action; other
  views retain existing image-reveal behavior.
- Atlas reading and chat use the existing Chronicle HTML safety rules through a
  shared document sanitizer. Protected Chronicle assets keep their scoped flow.

- Signing out clears Atlas page, relationship, metadata, and source-selection
  caches before another user signs in.

## Release

The module version is `0.3.2`. Publish the immutable `foundry-v0.3.2` tag
through the existing release workflow to create `shrecknet-foundry-v0.3.2.zip`
and the matching `module.json`. Foundry VTT 14 remains the minimum version.
No database migration is required. Rebuild/redeploy ShreckOS to install its
Elder proxy configuration and align any outer proxy timeouts.
