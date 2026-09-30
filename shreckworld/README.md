# ShreckWorld

ShreckWorld is Shrecknet's world-scoped knowledge service. It is presented by
the Shrecknet frontend as one application area; it has no separate login. It owns
its own worlds, uploaded sources, parsed chunks, embeddings, retrieval data,
and background embedding jobs. It does not call or share Librarian runtime
storage.

## First release

- Shrecknet administrators and world builders create and manage `ShreckWorld` records.
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

The Shrecknet frontend forwards its existing `Authorization: Bearer` access
token. ShreckWorld verifies it against Shrecknet's `/auth/jwks` signing keys;
there is no ShreckWorld password, cookie, or admin token. Authenticated users
can query; only `admin` and `world_builder` roles can manage sources.
