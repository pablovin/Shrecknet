# Shrecknet

Standalone Shrecknet core platform.

Current version: 0.5.5

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
pip install -e ".[test]"
uvicorn app.main:app --reload --port 8100
```

## Local Databases
Shrecknet database files should live physically in `shrecknet/databases/`.
The standalone Docker stack binds that host folder into the container as `/data`, so the SQLite files remain visible and copyable from the repo checkout.
