# shreckdoc

Storage-independent document algorithms shared by Librarian and ShreckWorld.
`shreckdoc.pipeline` owns the Docling adapter, normalized pages/sections/blocks,
semantic parent/child construction, contextual E5 text, and tokenizer-bounded
splitting. Normalized records retain optional Docling-native formatting, list
markers, caption references, table cell/span structure, and textless picture
records alongside the existing display text and provenance. These fields are
additive; chunk construction continues to use its established display-text and
context behavior. `shreckdoc.retrieval` owns named-term extraction, reciprocal rank
fusion, and diversity selection. The package has no application, Neo4j,
SQLAlchemy, Celery, or model-runtime imports at module import time.

`shreckdoc.structure_toc` extracts visible TOC rows as candidates, retains their
source block references, and maps printed labels only from unique title anchors or
corroborated within-range offsets. `shreckdoc.structure_headings` builds global
style, numbering, and repetition profiles while retaining rejected text in the
normalized document. These utilities report candidate evidence only; final
hierarchy resolution remains a separate step. ShreckWorld's
`pdf_structure_evidence` collector reads PDF outline destinations and printed page
labels independently of the selected content parser and never extracts page text.

Docling and a real tokenizer are supplied by the consuming service. Librarian
retains its existing import surface and graph orchestration; ShreckWorld owns
its SQL persistence, Qdrant projection, and strict inference. The adapter's
`require_complete=True` mode rejects incomplete conversion for ShreckWorld;
its default preserves Librarian compatibility.

Install locally before either application:

```bash
pip install -e ./shreckdoc
```

Compose/Docker install the same package using the `shreckdoc` additional build
context; it is not fetched from a public package index. See the canonical
[ShreckWorld architecture](../Documentation/Architecture/SHRECKWORLD.md) and
[Librarian embedding](../Documentation/Agents/Librarian/Embedding/Embedding.md).
