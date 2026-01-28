# 🏰 Shrecknet

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Node](https://img.shields.io/badge/node-20%2B-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688)
![React](https://img.shields.io/badge/React-Next.js-61DAFB)
![License](https://img.shields.io/badge/license-GPLv3-blue)
![Version](https://img.shields.io/badge/version-0.1.0-orange)

> *"In the realm of endless imagination, where chronicles are written by both quill and code, Shrecknet emerges as your faithful companion—a mystical forge where worlds take shape, legends are born, and epic tales unfold."*

## 🎭 Project Summary - The Legend Begins

Welcome, brave adventurer, to **Shrecknet**—a collaborative world-building and storytelling platform forged in the fires of modern AI and the ancient art of tabletop role-playing games.

### The Quest

Every great campaign needs a living, breathing world. Shrecknet is your **digital grimoire**, combining:
- 📚 **Wiki-style CMS** for organizing your realm's lore
- 🤖 **AI Sages** (agents) who help populate worlds and craft narratives
- 🔗 **Graph-powered knowledge** linking every concept, character, and chronicle
- ⚔️ **Session-to-Story Alchemy** transforming raw RPG sessions into polished novels

Whether you're a Dungeon Master weaving intricate plots, a novelist crafting epic sagas, or a worldbuilder documenting vast universes, Shrecknet serves as your **intelligent scribe and co-creator**.

## ⚡ Quick Start - Join the Adventure

**Deploy in 10-30 seconds** (after one-time setup):

```bash
# One-time: Build dependencies (15-30 minutes)
cd backend_2 && ./build-venv.sh --ml && cd ..

# Deploy (10-30 seconds!) ⚡
docker compose build && docker compose up -d

# Access your realm
open http://localhost:8000/docs
```

See [Quick Start Guide](Documentation/GettingStarted/QUICKSTART.md) for details or [Virtual Environment Deployment](Documentation/Deployment/VENV_DEPLOYMENT.md) for the complete guide.

## 🎲 Features - Your Arsenal

### Core Powers
- 🗺️ **Ontology-Based World Building**: Organize worlds through interconnected Ontologies, Instances, and rich Properties
- 🧠 **Semantic Knowledge Graph**: Neo4j-powered relationships between all your world's elements
- 🔍 **Intelligent Cross-Linking**: Automatic discovery and linking of related concepts
- 📊 **Multi-Modal Content**: Support for text, images, PDFs, and rich markdown
- 🔐 **Role-Based Access Control**: Admin, Editor, and Viewer roles with fine-grained permissions
- 📱 **Modern React Frontend**: Built with Next.js, TypeScript, and Tailwind CSS
- 🔄 **Background Job Processing**: Celery workers handle intensive AI and embedding tasks
- 💾 **Flexible Data Persistence**: SQLite for metadata, Neo4j for graphs, Redis for caching

### Legendary Abilities
- 🌌 **Embedding-Powered Search**: Vector similarity across all your content
- 📝 **Persistent Chat Sessions**: Conversational context preserved across interactions
- 📖 **PDF Library System**: Upload and query rulebooks with citation tracking
- 🎨 **Rich Text Editing**: Full markdown support with custom formatting
- 🔄 **Import/Export**: Backup and share your worlds effortlessly
- 🌐 **GraphRAG Integration**: Retrieval-augmented generation over your knowledge graph
- 📈 **Audit Logging**: Track all changes to your world's content

## 🤖 AI Agents - The Council of Sages

Shrecknet's AI agents are specialized NPCs who help you build and navigate your world:

### 👴 **Elder Agent** (Conversational AI)
*The wise sage who knows your world inside and out*

**Powers:**
- Answers questions about your world using semantic search
- Maintains persistent chat sessions with named conversations
- Provides source citations for all responses
- Supports custom personality and writing styles

**Pipeline:**
1. Receives your query
2. Searches the knowledge graph and vector embeddings
3. Generates contextual responses with OpenAI
4. Returns answers with source links and citations

**Use Cases:** Lore Q&A, character backstory exploration, world fact-checking

---

### 📚 **Librarian Agent** (Document Intelligence)
*The keeper of ancient tomes who extracts wisdom from your rulebooks*

**Powers:**
- Semantic search across embedded PDF documents
- Page-level chunking with precise citations
- Multi-book cross-referencing
- Configurable response styles

**Pipeline:**
1. User uploads PDF rulebooks to the library
2. Background jobs extract and embed content
3. Queries search across all embedded chunks
4. Returns comprehensive answers with page numbers

**Use Cases:** Rules lookups, mechanics explanations, sourcebook research

---

### ✍️ **Novelist Agent** (Story Transformer)
*The bard who transforms raw session notes into epic prose*

**Powers:**
- Converts RPG transcripts into novel-style chapters
- Applies narrative structure and pacing
- Incorporates world lore and character details
- Supports critic feedback loops

**Pipeline:**
1. Ingests raw session transcripts
2. Chunks and analyzes narrative flow
3. Rewrites in chosen literary style
4. Optionally applies editor notes and world context

**Use Cases:** Session recaps, campaign novelization, story polishing

---

### 🏗️ **Architect Agent** (World Builder)
*The master planner who structures and expands your realm*

**Powers:**
- Analyzes ontology structure and suggests improvements
- Generates new instances and relationships
- Proposes property expansions
- Maintains ontological consistency

**Pipeline:**
1. Scans existing world structure
2. Identifies gaps and opportunities
3. Generates structured suggestions
4. Stores proposals for review and approval

**Use Cases:** World expansion, consistency checking, structural analysis

---

### 📖 **Writer Agent** (Legacy - Backend 1)
*Analyzes and generates wiki pages*

**Pipeline:**
1. Reviews existing pages for completeness
2. Suggests new content and improvements
3. Generates draft pages as background jobs
4. Stores results for manual review

**Use Cases:** Content generation, page suggestions

## 🏗️ Backend Services - The Infrastructure

### Backend_2 (Primary - Recommended)
**Modern FastAPI service** with clean architecture and Neo4j integration

**API Endpoints:**
- `/auth` - Authentication and JWT token management
- `/users` - User management and profiles
- `/ontologies` - Ontology CRUD and queries
- `/ontology-instances` - Instance management with relationships
- `/notes` - Rich text note-taking with markdown
- `/admin-notes` - Admin-specific notes with special permissions
- `/games` - Game session management
- `/agents` - AI agent configuration and execution
- `/elder` - Elder agent queries and responses
- `/elder-chats` - Persistent chat session management
- `/librarian` - Librarian agent and library queries
- `/library` - PDF upload and document management
- `/novelist` - Novel generation from transcripts
- `/architect` - World structure analysis and suggestions
- `/graphrag` - Graph-based retrieval augmented generation
- `/background-jobs` - Job status and management
- `/backups` - World backup and restore
- `/imports` - Data import from various formats
- `/media` - Media file upload and serving
- `/notifications` - User notifications
- `/audit-logs` - Change tracking and audit trail
- `/page-visits` - Analytics and page tracking

**Technologies:**
- FastAPI 0.110 with async/await
- SQLAlchemy 2.0 with asyncio support
- Neo4j 5 graph database
- Celery for background jobs
- Redis for caching and job queue
- Langchain & OpenAI for AI features
- Sentence-Transformers for embeddings
- PyMuPDF & PyPDF2 for document processing

## 🚀 Deployment Guide

### Components to Deploy

A full Shrecknet deployment consists of:

1. **Backend_2 API** (Required) - Main FastAPI application
2. **Backend_2 Worker** (Required) - Celery worker for AI/embedding jobs
3. **Neo4j** (Required) - Graph database for ontologies
4. **Redis** (Required) - Message broker and cache
5. **Frontend** (Optional) - Next.js React application
6. **Backend** (Optional) - Legacy API for compatibility

### Configuration Files

#### Backend_2 Environment Variables

Create `.env` in the project root or set these environment variables:

```bash
# Database
BACKEND_2_DATABASE_URL=sqlite+aiosqlite:////data/backend_2.db
BACKEND_2_JOBS_DATABASE_URL=sqlite+aiosqlite:////data/backend_2_jobs.db

# Neo4j Graph Database
BACKEND_2_NEO4J_URI=bolt://neo4j:7687
BACKEND_2_NEO4J_USER=neo4j
BACKEND_2_NEO4J_PASSWORD=VeryStrongPass123
BACKEND_2_NEO4J_DATABASE=neo4j

# Celery / Redis
BACKEND_2_CELERY_BROKER_URL=redis://redis:6379/0
BACKEND_2_CELERY_RESULT_BACKEND=redis://redis:6379/1
BACKEND_2_CELERY_TASK_ALWAYS_EAGER=false

# Security
BACKEND_2_SECRET_KEY=your-secret-key-min-32-chars
BACKEND_2_ALGORITHM=HS256
BACKEND_2_ACCESS_TOKEN_EXPIRE_MINUTES=30

# AI Services (Optional)
BACKEND_2_OPENAI_API_KEY=sk-your-openai-key
BACKEND_2_OPENAI_MODEL=gpt-4o-mini

# Media Storage
BACKEND_2_MEDIA_ROOT=/app/media

# CORS (Development)
BACKEND_2_CORS_ALLOW_ORIGINS=http://localhost:3000
```

### Security Considerations

⚠️ **Before deploying to production:**

1. **Change default passwords**: Update Neo4j password in `docker-compose.yml`
2. **Generate strong secret keys**: Use `openssl rand -hex 32` for SECRET_KEY
3. **Configure CORS properly**: Set specific origins, not wildcards
4. **Use environment secrets**: Never commit API keys to git
5. **Enable HTTPS**: Use reverse proxy (nginx/traefik) with SSL certificates
6. **Restrict media access**: Configure proper permissions for `/media` endpoint
7. **Set up backups**: Regularly backup Neo4j, SQLite, and media volumes

## 🐳 Running with Docker (Recommended)

### Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+
- 3+ CPU cores
- 16GB RAM minimum (32GB recommended)
- 20GB disk space

### Quick Deploy

```bash
# One-time: Build dependencies (15-30 minutes)
cd backend_2
./build-venv.sh --ml
cd ..

# Build and start all services (10-30 seconds after first build!)
docker compose build
docker compose up -d

# Verify services are running
docker compose ps

# View logs
docker compose logs -f backend_2
```

### Step-by-Step Docker Deployment

#### Step 1: Prepare Environment

```bash
# Clone the repository
git clone https://github.com/pablovin/Shrecknet.git
cd Shrecknet

# Copy and edit configuration (optional - defaults work)
cp backend/.env.example backend/.env
# Edit backend/.env with your OpenAI API key if using AI features
```

#### Step 2: Build Dependencies (Fast Deploy Method)

```bash
# Build Python virtual environment with ML dependencies
cd backend_2
./build-venv.sh --ml

# This creates a .venv folder with all dependencies pre-installed
# Future builds will copy this folder instead of reinstalling (10-30s vs 15-30min!)
cd ..
```

#### Step 3: Build Docker Images

```bash
# Build all services
docker compose build

# Or build specific service
docker compose build backend_2
```

#### Step 4: Start Services

```bash
# Start in detached mode
docker compose up -d

# Start with logs visible (good for first run)
docker compose up

# Start specific services only
docker compose up -d backend_2 redis neo4j
```

#### Step 5: Verify Deployment

```bash
# Check service status
docker compose ps

# View logs
docker compose logs -f backend_2
docker compose logs -f backend_2_worker

# Test API
curl http://localhost:8000/health
```

#### Step 6: Access Services

- **API Documentation**: http://localhost:8000/docs
- **Neo4j Browser**: http://localhost:7474 (neo4j / VeryStrongPass123)
- **Media Files**: http://localhost:8000/media/

### Docker Management Commands

```bash
# View logs for all services
docker compose logs -f

# View logs for specific service
docker compose logs -f backend_2

# Restart a service
docker compose restart backend_2

# Stop all services (keeps data)
docker compose down

# Stop and remove all data (⚠️ WARNING!)
docker compose down -v

# Rebuild after code changes
docker compose build
docker compose up -d

# Execute commands in running container
docker compose exec backend_2 bash

# Scale workers (if needed)
docker compose up -d --scale backend_2_worker=3
```

### Data Persistence

All data persists across container restarts in Docker volumes:

- `backend-data` - SQLite databases
- `backend-media` - Uploaded files and images
- `neo4j-data` - Graph database
- `neo4j-logs` - Database logs
- `redis-data` - Cache and job queue

**To backup your data:**
```bash
# Backup volumes
docker run --rm -v shrecknet_backend-data:/data -v $(pwd):/backup ubuntu tar czf /backup/backend-data.tar.gz -C /data .
docker run --rm -v shrecknet_neo4j-data:/data -v $(pwd):/backup ubuntu tar czf /backup/neo4j-data.tar.gz -C /data .

# Restore volumes
docker run --rm -v shrecknet_backend-data:/data -v $(pwd):/backup ubuntu tar xzf /backup/backend-data.tar.gz -C /data
```

## 💻 Running Without Docker

Perfect for development or when you want full control.

### Prerequisites

- Python 3.11 or higher
- Node.js 20 or higher  
- Git

### Step 1: Install Dependencies

```bash
# Backend_2 (Primary)
cd backend_2
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[ml,test]"
cd ..

# Backend (Legacy - Optional)
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..

# Frontend (Optional)
cd frontend
npm install
cd ..
```

### Step 2: Start External Services

You'll need Redis, Neo4j, and optionally ChromaDB:

```bash
# Redis (Required for Backend_2)
docker run -d --name redis \
  -p 6379:6379 \
  redis:7-alpine

# Neo4j (Required for Backend_2)
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -v neo4j-data:/data \
  -e NEO4J_AUTH=neo4j/VeryStrongPass123 \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:5-community

# ChromaDB (Optional - for Backend legacy vector search)
docker run -d --name chromadb \
  -p 8001:8001 \
  chromadb/chroma:latest
```

### Step 3: Configure Environment

```bash
# Backend_2
cat > backend_2/.env << EOF
BACKEND_2_NEO4J_URI=bolt://localhost:7687
BACKEND_2_NEO4J_USER=neo4j
BACKEND_2_NEO4J_PASSWORD=VeryStrongPass123
BACKEND_2_CELERY_BROKER_URL=redis://localhost:6379/0
BACKEND_2_CELERY_RESULT_BACKEND=redis://localhost:6379/1
BACKEND_2_DATABASE_URL=sqlite+aiosqlite:///./backend_2.db
BACKEND_2_JOBS_DATABASE_URL=sqlite+aiosqlite:///./backend_2_jobs.db
BACKEND_2_OPENAI_API_KEY=sk-your-key-here
EOF

# Backend (if using)
cp backend/.env.example backend/.env
# Edit backend/.env with your settings

# Frontend (if using)
cp frontend/.env.local.example frontend/.env.local
# Edit frontend/.env.local - set NEXT_PUBLIC_API_URL if needed
```

### Step 4: Run Services

Open separate terminal windows/tabs for each service:

**Terminal 1 - Backend_2 API:**
```bash
cd backend_2
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 - Backend_2 Worker:**
```bash
cd backend_2
source .venv/bin/activate
celery -A app.celery_app worker --loglevel=info --concurrency=2
```

**Terminal 3 - Backend (Legacy - Optional):**
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

**Terminal 4 - Backend Worker (Legacy - Optional):**
```bash
cd backend
source .venv/bin/activate
celery -A app.task_queue.celery_app worker --loglevel=info
```

**Terminal 5 - Frontend (Optional):**
```bash
cd frontend
npm run dev
# Access at http://localhost:3000
```

### Step 5: Verify Everything Works

```bash
# Test Backend_2
curl http://localhost:8000/health
curl http://localhost:8000/docs  # View in browser

# Test Neo4j connection
curl http://localhost:7474  # Neo4j browser

# Test Frontend (if running)
curl http://localhost:3000
```

### Development Tips

- **Auto-reload**: Both FastAPI (with `--reload`) and Next.js (with `dev`) watch for file changes
- **API Docs**: Visit `/docs` for interactive Swagger UI
- **Database Tools**: Use Neo4j Browser at http://localhost:7474 to query the graph
- **Debugging**: Set `BACKEND_2_DEBUG=true` for verbose logging
- **Testing**: Run `pytest` in backend_2 directory (with venv activated)

## 📋 System Requirements

### Minimum Requirements
- **OS**: Linux, macOS, or Windows 10+
- **CPU**: 2 cores
- **RAM**: 8GB
- **Disk**: 10GB free space
- **Python**: 3.11+
- **Node.js**: 20+ (if running frontend)

### Recommended Requirements
- **OS**: Ubuntu 22.04 LTS or macOS 13+
- **CPU**: 4+ cores
- **RAM**: 16GB+
- **Disk**: 50GB+ SSD
- **Python**: 3.11 or 3.12
- **Node.js**: 20.x LTS

### Production Requirements
- **CPU**: 8+ cores
- **RAM**: 32GB+
- **Disk**: 100GB+ SSD with RAID
- **Network**: 100+ Mbps
- **OS**: Ubuntu 22.04 LTS (recommended)

## 🔧 Compatibility

### Python Dependencies
- **Python**: 3.10, 3.11, 3.12 (3.11 recommended)
- **FastAPI**: 0.110.x
- **SQLAlchemy**: 2.0.x
- **Neo4j Driver**: 5.x
- **Langchain**: 0.1-0.3
- **OpenAI**: 1.x
- **Celery**: 5.4.x

### Node.js Dependencies
- **Node.js**: 20.x LTS
- **Next.js**: 15.x
- **React**: 18.x
- **TypeScript**: 5.x

### Databases
- **Neo4j**: 5.x (Community or Enterprise)
- **Redis**: 7.x
- **SQLite**: 3.x (built-in)
- **PostgreSQL**: 14+ (optional, via DATABASE_URL)

### AI Services
- **OpenAI API**: GPT-4, GPT-4o, GPT-4o-mini
- **OpenAI Embeddings**: text-embedding-3-small/large
- **Sentence Transformers**: 2.2+ (local embeddings)

### Browsers
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

## 📄 License

This project is licensed under the **GNU General Public License v3.0** (GPLv3).

- ✅ Freedom to use, study, share, and modify
- ✅ Source code must remain open
- ✅ Derivative works must use GPLv3
- ✅ Commercial use allowed

See [LICENSE](LICENSE) file for full details.

## 👥 Authors & Contributors

**Created by Pablo Vin**
- 📧 Email: [pablovin@gmail.com](mailto:pablovin@gmail.com)
- 🔗 GitHub: [@pablovin](https://github.com/pablovin)

**Project**: [Shrecknet](https://github.com/pablovin/Shrecknet)

### Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Follow coding standards (see [AGENTS.md](Documentation/AIAgents/AGENTS.md))
4. Write tests for new features
5. Submit a Pull Request

## 📚 Documentation

All documentation has been organized in the **[Documentation](Documentation/)** folder:

- **[Getting Started](Documentation/GettingStarted/)** - Quick start guides and tutorials
- **[Deployment](Documentation/Deployment/)** - Docker, venv, and deployment workflows
- **[Backend](Documentation/Backend/)** - Backend API and service documentation
- **[Frontend](Documentation/Frontend/)** - Frontend application guides
- **[API Reference](Documentation/API/)** - Detailed API endpoint documentation
- **[AI Agents](Documentation/AIAgents/)** - Agent system documentation
- **[Architecture](Documentation/Architecture/)** - System architecture and design
- **[Database](Documentation/Database/)** - Database setup and migrations
- **[Implementation Notes](Documentation/ImplementationNotes/)** - Historical development notes

See the **[Documentation README](Documentation/README.md)** for a complete index of all available documentation.

## 🛠️ Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Find and kill process using port 8000
lsof -ti:8000 | xargs kill -9
```

**Neo4j connection failed:**
```bash
# Verify Neo4j is running
docker ps | grep neo4j
# Check credentials match in .env and docker-compose.yml
```

**Celery worker not processing jobs:**
```bash
# Verify Redis is running
redis-cli ping  # Should return "PONG"
# Check worker logs
docker compose logs backend_2_worker
```

**Build takes too long:**
- Use the .venv pre-build method: `cd backend_2 && ./build-venv.sh --ml`
- Ensure good internet connection for package downloads
- Consider using `--ml` flag only if you need embedding features

**Out of memory:**
- Reduce Neo4j heap size in docker-compose.yml
- Decrease Celery worker concurrency
- Close unused services

## 🎯 Version History

### v0.1.0 (Current)
- ✨ Initial public release
- 🏗️ Backend_2 with Neo4j graph database
- 🤖 Elder, Librarian, Novelist, and Architect agents
- 📚 PDF library system with embeddings
- 🔐 Role-based access control
- 🐳 Optimized Docker deployment
- ⚡ Lightning-fast build system

---

*May your worlds be vast, your stories legendary, and your dice rolls ever in your favor!* 🎲✨
