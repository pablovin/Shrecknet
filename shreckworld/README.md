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
python -m pip install -e ./shreckconfig -e ./shreckdoc -e './shreckworld[test]'
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
and request read-only rule review. The backward-compatible `single` retrieval
strategy remains the query default; opt-in `planned` retrieval and owner-scoped
asynchronous agent jobs produce structured evidence coverage without answer
synthesis. Query requests can select one, several, or all sources; grounded
`mode="answer"` remains available on the single strategy using the configured
ShreckLLM provider/model. `admin` and `world_builder` manage sources and
indexing. There is no separate password/admin token. `/health` checks liveness;
`/ready` verifies actual E5 inference and local parser-artifact presence.

The initial ShreckWorld LLM target defaults to `openai` / `gpt-5-nano` and can
be read or changed by `admin` and `world_builder` users through
`GET`/`PUT /admin/settings/model-targets/shreckworld_synthesizer`. It is stored
in the ShreckWorld database and shared by planner, coverage reviewer, and
grounded answer mode. Until changed through that API, the legacy
`SHRECKWORLD_SYNTHESIS_PROVIDER` and `SHRECKWORLD_SYNTHESIS_MODEL` environment
variables supply the initial target.

`SHRECKWORLD_SHRECKLLM_BASE_URL` defaults to `http://shreckllm:8110`; the
synthesis deadline defaults to 25 seconds and the evidence budget to 8,000
estimated tokens. Set the environment variables to match the configured
ShreckLLM deployment. The optional
`SHRECKLLM_INTERNAL_SERVICE_TOKEN` is forwarded for deployments using internal
service authentication. Evidence retrieval and original-source reading remain
available when ShreckLLM is unavailable.

Planned retrieval has separate controls: `SHRECKWORLD_QUERY_V3_PLANNING_ENABLED`
and `SHRECKWORLD_QUERY_V3_COVERAGE_REVIEW_ENABLED` default to `true`, and
`SHRECKWORLD_QUERY_V3_EVIDENCE_TOKEN_BUDGET` defaults to 8,000 estimated tokens.
The planned agent uses the configured ShreckLLM provider/model, but never runs
answer synthesis.

Browser CORS is configured only in the repository-root `.env`, read directly
through a read-only API container mount. See the
[shared CORS guide](../Documentation/Deployment/BROWSER_CORS.md) for settings and
container rollout; process environment overrides are not used for CORS.

Canonical contracts:

- [Architecture](../Documentation/Architecture/SHRECKWORLD.md)
- [ShreckWorld Agent](../Documentation/Agents/ShreckWorld/ShreckWorld%20Agent.md)
- [Upload/autofill](../Documentation/API/SHRECKWORLD_UPLOAD_API.md)
- [Indexing/jobs/readiness](../Documentation/API/SHRECKWORLD_INDEXING_API.md)
- [Reader/query/rule review](../Documentation/API/SHRECKWORLD_READER_API.md)
