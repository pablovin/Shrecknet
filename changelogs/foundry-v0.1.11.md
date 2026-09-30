# Foundry v0.1.11

Date: 2026-09-30

## Fixed

- Preserve the source-page card in the Narrative Scene rendering context so
  opening a scene does not fail with `sourcePageCard is not defined`.
- Give compact Narrative Scene rows enough height for their title and metadata,
  preventing adjacent rows from visually overlapping.
- Avoid rendering an entity avatar a second time when it is already the page's
  large hero image.
- Preserve the page's Narrative Scene array while preparing page properties and
  related-record presentation.

## Release

- The Foundry module manifest version is `0.1.11`; release it with the immutable
  `foundry-v0.1.11` tag and matching ZIP asset.
