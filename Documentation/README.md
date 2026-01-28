# Shrecknet Documentation

Welcome to the Shrecknet documentation! This directory contains all organized documentation for the project.

## 📚 Documentation Structure

### [Getting Started](./GettingStarted/)
New to Shrecknet? Start here!
- [Quick Start Guide](./GettingStarted/QUICKSTART.md) - Get up and running quickly

### [Deployment](./Deployment/)
Everything you need to deploy Shrecknet
- [Docker Deployment](./Deployment/DOCKER.md) - Containerized deployment guide
- [Virtual Environment Deployment](./Deployment/VENV_DEPLOYMENT.md) - Traditional venv setup
- [Deployment Workflow](./Deployment/DEPLOYMENT_WORKFLOW.md) - Step-by-step deployment process
- [Docker Optimization](./Deployment/DOCKER_OPTIMIZATION.md) - Performance optimizations
- [Deployment Optimization Summary](./Deployment/DEPLOYMENT_OPTIMIZATION_SUMMARY.md)

### [Backend](./Backend/)
Backend service documentation (FastAPI)
- [Backend README](./Backend/README.md) - Main backend documentation
- [Complete Endpoints Reference](./Backend/COMPLETE_ENDPOINTS_REFERENCE.md) - All API endpoints
- [Admin Notes API](./Backend/ADMIN_NOTES_API.md) - Admin functionality
- [Timezone Enforcement](./Backend/TIMEZONE_ENFORCEMENT.md) - Timezone handling

### [Frontend](./Frontend/)
Frontend application documentation (Next.js/React)
- [Frontend README](./Frontend/README.md) - Main frontend documentation

### [API](./API/)
Detailed API documentation for specific features
- [Favorite API Quick Reference](./API/FAVORITE_API_QUICK_REFERENCE.md)
- [Forum API Documentation](./API/FORUM_API_DOCUMENTATION.md)
- [Library API](./API/LIBRARY_API.md)
- [Backup API](./API/BACKUP_API.md)
- [Admin Clear Endpoints](./API/ADMIN_CLEAR_ENDPOINTS.md)
- [Architect API Examples](./API/ARCHITECT_API_EXAMPLES.md)
- [Favorite Ontology Instances API](./API/FAVORITE_ONTOLOGY_INSTANCES_API.md)

### [AI Agents](./AIAgents/)
Documentation for AI agent systems
- [Agents Overview](./AIAgents/AGENTS.md) - Main agents documentation
- [Conversational Agent](./AIAgents/conversational_agent.md) - Chat functionality
- [Writer Agent](./AIAgents/writer_agent.md) - Content generation
- [Architect Entity IDs](./AIAgents/architect_entity_ids.md) - Entity management

### [Architecture](./Architecture/)
System architecture and design documentation
- [GraphRAG](./Architecture/GRAPHRAG.md) - Graph-based retrieval augmented generation
- [Celery](./Architecture/CELERY.md) - Background job processing
- [Linking](./Architecture/LINKING.md) - Entity linking system
- [Agentic Jobs](./Architecture/AGENTIC_JOBS.md) - AI agent job management
- [Architect V2 Pipeline](./Architecture/ARCHITECT_V2_PIPELINE.md)
- [Architect V2 Frontend Guide](./Architecture/ARCHITECT_V2_FRONTEND_GUIDE.md)
- [Architect Word Chunking Examples](./Architecture/ARCHITECT_WORD_CHUNKING_EXAMPLES.md)
- [Architect Monitoring](./Architecture/ARCHITECT_MONITORING.md)
- [Architect Monitoring Quick Reference](./Architecture/ARCHITECT_MONITORING_QUICKREF.md)

### [Database](./Database/)
Database setup, migrations, and management
- [Embedding](./Database/EMBEDDING.md) - Vector embeddings setup
- [Embedding Implementation](./Database/EMBEDDING_IMPLEMENTATION.md)
- [Migration Embedding](./Database/MIGRATION_EMBEDDING.md)
- [Password Migration](./Database/PASSWORD_MIGRATION.md)
- [Neo4j Event Loop Fix](./Database/NEO4J_EVENT_LOOP_FIX.md)
- [Import Documentation](./Database/IMPORT_DOCUMENTATION.md)

### [Implementation Notes](./ImplementationNotes/)
Historical implementation notes and summaries (archived for reference)
- Various feature implementations, bug fixes, and enhancement summaries
- Useful for understanding development history and decisions

## 🔍 Quick Links

- **Main Project README**: [../README.md](../README.md)
- **License**: [../LICENSE](../LICENSE)

## 📖 Documentation Guidelines

When adding new documentation:
1. Place it in the appropriate category folder
2. Update the relevant section in this README
3. Use clear, descriptive filenames
4. Include cross-references to related documentation
5. Keep documentation up-to-date with code changes

## 🤝 Contributing

See the main [README](../README.md) for contribution guidelines.
