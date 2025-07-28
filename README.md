# Shrecknet

![Python](https://img.shields.io/badge/python-3.11-blue)
![Node](https://img.shields.io/badge/node-20-green)
![License](https://img.shields.io/badge/license-GPLv3-blue)

Shrecknet is a collaborative world building and story telling platform. It mixes a wiki style CMS with AI agents that help populate game worlds, create content and even craft novels out of your play sessions.

## Features

- **CRM like wiki** with Worlds, Concepts, Characteristics and Pages
- **Automatic cross linking** and vector search powered by Celery workers
- **Conversational, Specialist, Writer and Novelist AI agents**
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

```bash
docker compose up --build
```
The frontend will be at `http://localhost:3000` and the backend API at `http://localhost:8000`.

## Running without Docker

1. Install Python 3.11 and Node 20
2. `cd backend && pip install -r requirements.txt`
3. `cd ../frontend && npm install`
4. Start Redis and Chroma databases
5. In one shell run `uvicorn app.main:app --reload`
6. In another shell run `npm run dev` inside `frontend`
7. Optionally start Celery with `celery -A app.task_queue.celery_app worker --loglevel=info`

## Contact
For questions or feedback please reach out to [pablovin@gmail.com](mailto:pablovin@gmail.com)
