# ShreckWorld

ShreckWorld is Shrecknet's independently stored world-knowledge service. It uses
Shrecknet identity and shares document algorithms with Librarian through
`shreckdoc`, while owning SQLite structured records, Qdrant semantic vectors,
private source files/covers, and indexing jobs. It imports no Librarian graph
storage or Neo4j runtime.

Each source item references a Shrecknet ontology directly through `ontology_id`.
Atlas uses that ontology for graph content; ShreckWorld uses it for source items.
There is no separate world/library creation or provisioning step. The direct API
uses `/ontologies/{id}/items`; old collection routes remain for compatibility.
Deploy the service before the UI and preview/migrate existing items using the
[reader contract](../Documentation/API/SHRECKWORLD_READER_API.md#legacy-item-migration).

Sources support PDF, DOCX, Markdown, and text. Compose enables experimental
PyMuPDF layout-aware PDF chunking; standalone defaults to Docling.
`SHRECKWORLD_PDF_PARSER=docling` restores Docling for comparison. Contextual,
tokenizer-bounded E5 children retain separate display text and semantic parents.
OCR is disabled and extraction quality varies by document.
Hash embeddings are no longer a fallback. Existing hash indexes require
reindexing before semantic retrieval; original source reading remains available.

Compose builds install required CPU PyTorch, Sentence Transformers, Docling,
and the shared package. Model prefetch prepares the pinned E5 snapshot, reranker,
and Docling artifacts; API/worker runtime uses the local cache. The optional GPU
overlay also supports ShreckWorld. See the
[indexing API](../Documentation/API/SHRECKWORLD_INDEXING_API.md) for rollout,
configuration, failure states, progress/timing fields, and rollback.
The authenticated [Book Map reader contract](../Documentation/API/SHRECKWORLD_READER_API.md#book-map)
documents structural navigation and the embedding-free backfill command.

CPU embedding uses ONNX Runtime by default. In Shrecknet's configuration frontend,
select `Shreckworld → Embedding backend` to choose `onnx`, `torch`, or `openvino`.
Compose images include all three; after changing, drain indexing, run prefetch,
restart API/worker, and reindex sources. An explicit
`SHRECKWORLD_EMBEDDING_BACKEND` environment value overrides the frontend setting.
Local OpenVINO installs use `./shreckworld[openvino]`.
Prefetch exports portable models; each host optimizes at load
time. See [CPU backend setup](../Documentation/API/SHRECKWORLD_INDEXING_API.md#portable-cpu-backends)
for compatibility and rollout details. Terminal attempts emit a
`[SHRECKWORLD_INDEXING_SUMMARY]` JSON console log with phase and parser timings.

For a local installation from the repository root:

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.6,<2.9' torchvision
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

The initial ShreckWorld LLM target defaults to `openrouter` / `deepseek/deepseek-v4.1-flash` and can
be read or changed by `admin` and `world_builder` users through
`GET`/`PUT /admin/settings/model-targets/shreckworld_synthesizer`. It is stored
in the ShreckWorld database and shared by planner, coverage reviewer, and
grounded answer mode. Until changed through that API, the legacy
`SHRECKWORLD_SYNTHESIS_PROVIDER` and `SHRECKWORLD_SYNTHESIS_MODEL` environment
variables supply the initial target.

`SHRECKWORLD_SHRECKLLM_BASE_URL` defaults to `http://shreckllm:8110`; the
synthesis deadline defaults to 600 seconds and the evidence budget to 8,000
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

Companion retrieval uses the existing `single` evidence strategy for an individual
planned question and reserves `planned` for explicit deep research. Companion
owns cross-source evidence review and public-Web recovery. See the
[Companion execution contract](../Documentation/Agents/Companion/Companion.md#evidence-first-query-pipeline).
