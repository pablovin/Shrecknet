# Foundry v0.3.1

## Fixed

- Remove the obsolete `themes/terminal/theme.json` release validation that
  prevented packaging the current module.
- Run module tests before publishing, including manifest asset and version checks.
- Attach `module.json` alongside the release ZIP for installation from a published
  release manifest.

## Release

Publish the immutable `foundry-v0.3.1` tag after merging. The workflow creates
`shrecknet-foundry-v0.3.1.zip`; updating the source manifest alone does not make
the download available. Foundry VTT 14 remains the minimum supported version.
No backend migration or configuration changes are required.
