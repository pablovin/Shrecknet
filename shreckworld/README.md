# ShreckWorld

ShreckWorld is Shrecknet's independently stored world-knowledge service. It uses
Shrecknet identity and shares document algorithms with Librarian through
`shreckdoc`, while owning SQLite structured records, Qdrant semantic vectors,
private source files/covers, and indexing jobs. It imports no Librarian graph
storage or Neo4j runtime.

Sources support PDF, DOCX, Markdown, and text. PDF indexing uses Docling layout,
headings, tables, and bounding boxes; contextual, tokenizer-bounded E5 children
retain separate display text and complete semantic parents. OCR is disabled.
Hash embeddings are no longer a fallback. Existing hash indexes require
reindexing before semantic retrieval; original source reading remains available.

Compose builds install required CPU PyTorch, Sentence Transformers, Docling,
and the shared package. Model prefetch prepares the pinned E5 snapshot, reranker,
and Docling artifacts; API/worker runtime uses the local cache. The optional GPU
overlay also supports ShreckWorld. See the
[indexing API](../Documentation/API/SHRECKWORLD_INDEXING_API.md) for rollout,
configuration, failure states, progress/timing fields, and rollback.

For a local installation from the repository root:

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
python -m pip install -e ./shreckdoc -e './shreckworld[test]'
export HF_HOME="$PWD/shreckworld/models/hf"
export HF_HUB_CACHE="$HF_HOME"
export SHRECKWORLD_DOCLING_ARTIFACTS_PATH="$PWD/shreckworld/models/docling"
export SHRECKWORLD_SHRECKNET_JWKS_URL=http://localhost:8100/auth/jwks
cd shreckworld
python -m app.prefetch
uvicorn app.main:app --reload --port 8120
# In a second shell with the same environment:
# python -m app.worker
```

Authenticated Shrecknet users can discover/read sources, ask evidence questions,
and request read-only rule review. Query requests can select one, several, or all
sources; evidence mode is the default, while grounded answer mode uses the
configured ShreckLLM provider/model. `admin` and `world_builder` manage sources
and indexing. There is no separate password/admin token. `/health` checks liveness;
`/ready` verifies actual E5 inference and local parser-artifact presence.

Answer-mode defaults are `SHRECKWORLD_SHRECKLLM_BASE_URL=http://shreckllm:8110`,
`SHRECKWORLD_SYNTHESIS_PROVIDER=openai`, `SHRECKWORLD_SYNTHESIS_MODEL=gpt-5-nano`,
and a 25-second synthesis deadline with an 8,000 estimated-token evidence budget. Set these environment variables to
match the configured ShreckLLM deployment. The optional
`SHRECKLLM_INTERNAL_SERVICE_TOKEN` is forwarded for deployments using internal
service authentication. Evidence retrieval and original-source reading remain
available when ShreckLLM is unavailable.

Canonical contracts:

- [Architecture](../Documentation/Architecture/SHRECKWORLD.md)
- [Upload/autofill](../Documentation/API/SHRECKWORLD_UPLOAD_API.md)
- [Indexing/jobs/readiness](../Documentation/API/SHRECKWORLD_INDEXING_API.md)
- [Reader/query/rule review](../Documentation/API/SHRECKWORLD_READER_API.md)
