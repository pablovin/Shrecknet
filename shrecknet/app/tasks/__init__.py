"""Background tasks for backend_2."""

# Import submodules so Celery autodiscovery registers task functions.
from . import (
    neo4j_embedding,
    ontology_links,
    pdf_embedding,
    library_metadata,
    architect_analysis,
    architect_generation,
    backup_tasks,
    novelist,
    ontology_instance_clear,
    companion_orchestrator,
    librarian_embedding_package,
)

__all__ = [
    "ontology_links",
    "neo4j_embedding",
    "pdf_embedding",
    "library_metadata",
    "architect_analysis",
    "architect_generation",
    "backup_tasks",
    "novelist",
    "ontology_instance_clear",
    "companion_orchestrator",
    "librarian_embedding_package",
]
