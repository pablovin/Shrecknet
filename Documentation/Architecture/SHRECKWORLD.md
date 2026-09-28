# ShreckWorld Phase 1

## Purpose

ShreckWorld is a standalone world-knowledge service. It replaces the
Librarian document-query responsibility over time, but has no Librarian runtime
dependency. It owns its ShreckWorld records, source PDFs, parsed chunks,
embeddings, vector data, retrieval jobs, and query provenance.

The existing Shrecknet `World` model remains an ontology-grouping concept.
`ShreckWorld` is deliberately a separate name and persistence boundary.

## Contract

The service lives in `shreckworld/` and is exposed on host port `8121` in Compose (container port `8120`).
The current standalone admin boundary uses `X-ShreckWorld-Admin-Token`.

| Purpose | Endpoint |
| --- | --- |
| Create/list ShreckWorlds | `POST` / `GET /admin/shreckworlds` |
| Read/update/delete one | `GET` / `PATCH` / `DELETE /admin/shreckworlds/{shreckworld_id}` |
| Upload/list sources | `POST` / `GET /admin/shreckworlds/{shreckworld_id}/library-items` |
| Edit/delete a source | `PATCH` / `DELETE /admin/shreckworlds/{shreckworld_id}/library-items/{item_id}` |
| Start/status embedding | `POST .../{item_id}/embed`, `GET .../{item_id}/embedding-status` |
| Query scoped sources | `POST /shreckworlds/{shreckworld_id}/query` |

Web-search configuration is persisted with `disabled`, `fallback`, and
`supplemental` policy values. Phase 1 does not call a web-search provider.

## Data, jobs, and retrieval

Uploaded PDFs live under `shreckworld/media/`. The independent ShreckWorld SQL
database owns source metadata, document chunks, vectors, and job state. The
Celery worker parses PDFs, chunks extractable text, and creates vectors. The optional sentence-transformer backend uses E5-compatible prefixes when the
`semantic` package extra is deployed. The default deterministic hash-vector
embedding keeps offline ingestion available, but has lower retrieval quality and
should be monitored operationally.

A query filters chunks by the requested `shreckworld_id` and active embedded
source before ranking. Returned citations are server-generated and include source
item, page, chunk, excerpt, relevance score, and authority metadata.

## Security and future work

Set `SHRECKWORLD_ADMIN_TOKEN` to a strong secret. A future Shrecknet identity
bridge must preserve this authorization boundary. Character embodiments, rules
resolution, scene mutations, Foundry, web execution, and Librarian removal are
out of scope. Librarian migration starts only after an explicit import and
answer/citation parity workflow exists.

## Related documentation

- [Shrecknet architecture](./SHRECKNET_ARCHITECTURE.md)
- [Librarian retrieval migration reference](../Agents/Librarian/Retrieval/Retrieval.md)
