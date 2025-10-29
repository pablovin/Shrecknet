# Shrecknet

![Python](https://img.shields.io/badge/python-3.11-blue)
![Node](https://img.shields.io/badge/node-20-green)
![License](https://img.shields.io/badge/license-GPLv3-blue)

Shrecknet is a collaborative world building and story telling platform. It mixes a wiki style CMS with AI agents that help populate game worlds, create content and even craft novels out of your play sessions.

## Features

- **CRM like wiki** with Worlds, Concepts, Characteristics and Pages
- **Automatic cross linking** and vector search powered by Celery workers
- **Conversational, Specialist, Writer and Novelist AI agents**
- **Librarian agents** for intelligent Q&A from embedded PDF rulebooks
- **Optional world embeddings** can be linked to agents to provide extra lore
- Import/export utilities and example data
- Docker based development environment

## Agents

### Conversational Agent
Talks about your world using the vector database for context.
Pipeline:
1. Receive user message
2. Query Chroma vector DB for relevant pages
3. Generate answer with OpenAI and provide source links

#### TODO
- Support multiple models
- Better chat history visualisation

### Specialist Agent
Uses an independent knowledge base for in depth Q&A.
Pipeline:
1. Query specialist vectors
2. Apply personality prompts
3. Return sources used

#### TODO
- Interface to upload more documents
- Fine tune per world personalities

### Writer Agent
Analyzes and generates wiki pages.
Pipeline:
1. Analyze pages to suggest new content
2. Generate new or updated pages as jobs
3. Store results for review

#### TODO
- Bulk accept suggestions UI
- Smarter merge strategies

### Novelist Agent
Turns RPG transcripts into novel style chapters.
Pipeline:
1. Split transcript into chunks
2. Summarise and rewrite with OpenAI
3. Optionally apply critic notes and world info

#### TODO
- Chapter outlining assistant
- Export to e‑book formats

### Librarian Agent
Answers questions from embedded PDF rulebooks and game materials.
Pipeline:
1. Semantic search across embedded PDF chunks
2. Generate comprehensive answers with page citations
3. Apply agent personality and writing style

#### Features
- Upload PDF rulebooks to library
- Background embedding jobs for PDFs
- Page-level chunking for precise citations
- Multi-book search across ontologies
- Configurable writing styles for responses

#### TODO
- Support for multiple embedding models
- Advanced chunking strategies (sections, tables)
- Cross-reference detection across books

## Configuration
Set the following environment variables or create a `.env` file in `backend`:

- `DATABASE_URL` – Database connection (defaults to in memory SQLite)
- `OPENAI_API_KEY` – API key for OpenAI models
- `OPEN_AI_MODEL` – Model name, e.g. `gpt-4o`
- `CELERY_BROKER_URL` – URL of the Redis broker
- `CELERY_RESULT_BACKEND` – Result backend
- `VECTOR_DB_URL` / `VECTOR_DB_PORT` – Chroma database location

Chat history, job files and vector DB data are stored under `backend/data`.

## Running with Docker

The docker-compose setup includes all required services with persistent data storage:
- **Frontend** (port 3000)
- **Backend** (port 8000) - Main API
- **Backend_2** (port 8080) - Ontology management with Neo4j
- **Redis** - Message broker with persistent storage
- **Neo4j** - Graph database with persistent storage (http://localhost:7474, bolt://localhost:7687)
- **ChromaDB** - Vector database with persistent storage
- **Celery workers** - Background task processors for both backends

```bash
docker compose up --build
```

**Data Persistence:**
All data is persisted using Docker volumes:
- Neo4j data is stored in the `neo4j-data` volume
- Redis data is stored in the `redis-data` volume
- ChromaDB data is stored in `./backend/data/vector_db`
- Media files are stored in `./backend_2/media`

You can safely stop and restart the containers without losing data:
```bash
docker compose down
docker compose up
```

**Note:** If you encounter SSL certificate errors during the build process in certain environments, you can work around this by building with `--network=host` flag or configuring pip to trust PyPI:
```bash
docker compose build --build-arg PIP_TRUSTED_HOST="pypi.org pypi.python.org files.pythonhosted.org"
```

**Service URLs:**
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- Backend_2 API: `http://localhost:8080`
- Neo4j Browser: `http://localhost:7474` (credentials: neo4j/VeryStrongPass123)

## Running without Docker

1. Install Python 3.11 and Node 20
2. `cd backend && pip install -r requirements.txt`
3. `cd ../frontend && npm install`
4. Start Redis, Chroma, and Neo4j databases:
   ```bash
   # Redis
   docker run -d -p 6379:6379 redis:7-alpine
   
   # ChromaDB
   docker run -d -p 8001:8001 chromadb/chroma:latest
   
   # Neo4j (for backend_2)
   docker run -d \
     -p 7474:7474 -p 7687:7687 \
     -v neo4j-data:/data \
     -e NEO4J_AUTH=neo4j/VeryStrongPass123 \
     neo4j:5-community
   ```
5. In one shell run `uvicorn app.main:app --reload` from the `backend` directory
6. In another shell run `uvicorn app.main:app --reload --port 8080` from the `backend_2` directory
7. In another shell run `npm run dev` inside `frontend`
8. Optionally start Celery workers:
   ```bash
   # Backend worker
   cd backend && celery -A app.task_queue.celery_app worker --loglevel=info
   
   # Backend_2 worker
   cd backend_2 && celery -A app.celery_app worker --loglevel=info
   ```

## Contact
For questions or feedback please reach out to [pablovin@gmail.com](mailto:pablovin@gmail.com)
