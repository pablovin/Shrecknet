# Shrecknet

Standalone Shrecknet core platform.

Current version: 0.5.7

Architect scene-centric chunking docs: `../Documentation/Agents/Architect/Scene Chunking.md`

## Responsibilities
- Identity and user management
- Worlds, ontologies, entities, relationships
- Living graph + embeddings + retrieval APIs
- Agent orchestration APIs (Elder, Architect, Novelist, Librarian)
- Domain event publishing

## Run
```bash
cd shrecknet
pip install -e ../shreckconfig -e ../shreckdoc -e ".[test]"
uvicorn app.main:app --reload --port 8100
```

## Local Databases
Shrecknet database files should live physically in `shrecknet/databases/`.
The standalone Docker stack binds that host folder into the container as `/data`, so the SQLite files remain visible and copyable from the repo checkout.

## Novelist context configuration

Novelist V5 extracts evidence globally, directs chronological literary arcs, and
writes from compact arc assignments and precise source passages.
`novelist_analysis_context_tokens` and `novelist_writer_context_tokens` default to
64,000 (minimum 24,000); configure them through the runtime configuration API within
the selected models' capacities. See the [Novelist contract](../Documentation/Agents/Novelist/Novelist.md)
for context preflight, PDF provenance, debug files, and worker rollout.

## Shreckworld indexing

ShreckWorld indexing is configured through the administrator configuration
frontend's **ShreckWorld Indexing** group. It selects the PDF parser, parser
device, table reconstruction and TableFormer mode, embedding backend, and
embedding device. Shrecknet publishes the non-secret selections under
`databases/service-config/shreckworld.json`; the standalone ShreckWorld services
read it through a read-only mount. Changes require restarting its API/worker;
parser changes require re-indexing existing sources, and backend/device changes
may require model prefetch. See the
[configuration contract](../Documentation/API/SHRECKWORLD_INDEXING_API.md#portable-cpu-backends).

## Browser CORS

Configure CORS only in the repository-root `.env`; the API reads its read-only
container mount directly. CORS settings are unavailable through `/config` and
process environment overrides. See the [shared CORS guide](../Documentation/Deployment/BROWSER_CORS.md)
for rollout, restart behavior, and legacy database cleanup.

## Companion retrieval budgets

Companion reuses Atlas plans and retrieves individual book questions through the
light evidence path. One router selects internal tools, subjective character memory
and Web queries; tools run concurrently before one Markdown synthesis. There is no
assessment, memory vote or claim verifier. Citation/access checks and optional rich
rendering are deterministic; rapport updates asynchronously after commit. One
truncated synthesis retry retains its evidence. Per-call defaults remain ten minutes;
saved timeout/output settings are preserved. See the
[Companion contract](../Documentation/Agents/Companion/Companion.md) and
[configuration rollout](../Documentation/Agents/Companion/Model%20Configuration.md#ten-minute-call-rollout).
