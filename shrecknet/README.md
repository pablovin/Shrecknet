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

## Browser CORS

Configure CORS only in the repository-root `.env`; the API reads its read-only
container mount directly. CORS settings are unavailable through `/config` and
process environment overrides. See the [shared CORS guide](../Documentation/Deployment/BROWSER_CORS.md)
for rollout, restart behavior, and legacy database cleanup.
