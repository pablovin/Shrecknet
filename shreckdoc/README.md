# shreckdoc

Storage-independent document algorithms shared by Librarian and ShreckWorld.
`shreckdoc.pipeline` owns the Docling adapter, normalized pages/sections/blocks,
semantic parent/child construction, contextual E5 text, and tokenizer-bounded
splitting. `shreckdoc.retrieval` owns named-term extraction, reciprocal rank
fusion, and diversity selection. The package has no application, Neo4j,
SQLAlchemy, Celery, or model-runtime imports at module import time.

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
