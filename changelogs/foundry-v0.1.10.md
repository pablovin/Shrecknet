# Foundry v0.1.10

Date: 2026-09-30

## Improved browsing

- Render folders, pages, related records, Narrative Scene sources, and milestone
  relations as image cards that open their underlying pages.
- Resolve Shrecknet media links against the configured server origin, including
  legacy localhost media links, so Foundry loads images from the selected
  deployment.
- Display entity properties with their definition names and provide linked
  breadcrumbs for page folders.
- Show loading feedback and scrollbars for long Shrecknet content.

## Narrative Scenes

- Group scenes under collapsible source-page panels and use compact scene rows
  that do not overlap their descriptions.
- Show a scene's source, related records, and milestone relations as card grids.
- Add Previous scene and Next scene links when local ordering provides them.

## Search

- Search pages and Narrative Scene names together, with page results first and
  scene hits grouped by their source page.
- Intercept Enter in module search fields so searches remain within Foundry and
  do not navigate the browser URL.

## Release

- The Foundry module manifest version is `0.1.10`; release it using the immutable
  `foundry-v0.1.10` tag and its matching ZIP asset.
