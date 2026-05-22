# Elder Embeddings Lifecycle

This phase covers ontology + GraphRAG embedding endpoints used to keep Elder retrieval fresh.

## SDK methods and endpoint coverage

| SDK method | Endpoint |
|---|---|
| `sdk.embeddings.stats(ontology_id)` | `GET /ontologies/{ontology_id}/embedding-stats` |
| `sdk.embeddings.trigger(ontology_id)` | `POST /ontologies/{ontology_id}/trigger-embedding` |
| `sdk.embeddings.recent_jobs(ontology_id)` | `GET /ontologies/{ontology_id}/embedding-jobs` |
| `sdk.embeddings.embed_node(node_id, ontology_id)` | `POST /graphrag/embed/node` |
| `sdk.embeddings.embed_ontology(ontology_id, batch_size)` | `POST /graphrag/embed/ontology` |
| `sdk.embeddings.backfill_chunks(ontology_id, batch_size)` | `POST /graphrag/embed/ontology/backfill-chunks` |
| `sdk.embeddings.reset_ontology_embeddings(ontology_id)` | `POST /graphrag/embed/ontology/reset` |
| `sdk.embeddings.ensure_index()` | `POST /graphrag/index/ensure` |

## Example

```bash
python python_sdk/examples/07_elder/01_embeddings_lifecycle.py
```

## Notes

- Use `SHRECKNET_RESET_EMBEDDINGS=1` only when intentionally resetting embeddings.
- Librarian PDF embedding endpoints are intentionally out of scope for this phase.
