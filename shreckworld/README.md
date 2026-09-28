# ShreckWorld

ShreckWorld is Shrecknet's standalone, world-scoped knowledge service. It owns
its own worlds, uploaded sources, parsed chunks, embeddings, retrieval data,
and background embedding jobs. It does not call or share Librarian runtime
storage.

## First release

- Admins create and manage `ShreckWorld` records.
- Admins upload PDF library items to one ShreckWorld and trigger embedding.
- Each item is parsed into source-owned chunks and embedded in ShreckWorld's
  SQL-backed vector store.
- `POST /shreckworlds/{shreckworld_id}/query` retrieves only that
  ShreckWorld's embedded sources and returns page-level provenance.
- Web-search policy is persisted but no web provider is called yet.

Run locally:

```bash
cd shreckworld
pip install -e ".[test]"
SHRECKWORLD_ADMIN_TOKEN=local-dev-token uvicorn app.main:app --reload --port 8120
```

Use `X-ShreckWorld-Admin-Token` for `/admin/*` endpoints. Production must set
an unguessable token; a Shrecknet identity bridge is a later integration, not a
substitute for authorization in this standalone service.
