# Book Map V4 synthetic fixtures

These fixtures are authored from synthetic source snippets and normalized
extraction evidence. They contain no external book text and make no claims about
real parser accuracy. They are shared starting points for resolver tests; the V4 resolver and ownership tests consume these evidence contracts.

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

## Local Call of Cthulhu regression

`tests/test_book_map_ownership_regressions.py` includes synthetic cases matching
the reported page destinations and an optional regression against retained local
Call of Cthulhu extraction artifacts. Set `SHRECKWORLD_COC_ARTIFACT_DIR` to a
generation directory containing `book_map_v4.json`, `book_map_evidence.json` and
`normalized_document.json`, or keep the extraction under `shreckworld/media/`
for discovery. The local regression skips when the source artifacts are absent;
no copyrighted source text or original media is added to the test fixtures.
It verifies Contents pages 8–9, Chapter 3 ownership of page 35, reliable splitting
of pages 275/278 across Chapter 14, and text recoverability on pages 9/50/51/406.
