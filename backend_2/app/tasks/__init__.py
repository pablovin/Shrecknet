"""Background tasks for backend_2."""

# Import submodules so Celery autodiscovery registers task functions.
from . import (
    neo4j_embedding,
    ontology_links,
    pdf_embedding,
    library_metadata,
    architect_analysis,
    architect_generation_v2,
    backup_tasks,
    novelist,
)

__all__ = [
    "ontology_links",
    "neo4j_embedding",
    "pdf_embedding",
    "library_metadata",
    "architect_analysis",
    "architect_generation_v2",
    "backup_tasks",
    "novelist",
]
