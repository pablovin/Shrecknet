# Book Map V4 synthetic fixtures

These fixtures are authored from synthetic source snippets and normalized
extraction evidence. They contain no external book text and make no claims about
real parser accuracy. They are shared starting points for resolver tests; the
current V3 implementation does not consume them yet.

## `evidence_cases.json`

The fixture contains three physical pages with distinct printed labels, a
partial nested PDF outline, a two-column printed contents sample with one
ambiguous printed label, and heading candidates from body text. It also includes
paragraph, structured-table, textless-image, caption and unknown blocks. The
expected outcomes are deliberately evidence-focused:

- Preserve bookmark destinations and levels, including same-page anchors.
- Keep the duplicated printed label unresolved instead of guessing an offset.
- Keep the contents text distinguishable from body-heading candidates.
- Preserve the table cells, textless image, separate caption and unknown block.
- Retain page numbers, printed labels and source reading order for every block.
- Do not infer a final hierarchy or claim page coverage from this input alone.

The JSON is a test input vocabulary, not a public or persisted contract. B01
defines the typed shared contract and may replace this shape while retaining the
same synthetic evidence and expectations.

The `contracts/` directory contains compact V3 and V4 golden JSON. V3 is the
unchanged public response shape. V4 freezes the initial typed envelope and node
fields for later adapter, persistence, and API work; it does not imply that V4
routes or indexing are enabled.
