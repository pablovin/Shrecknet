"""Public Librarian job entrypoint.

The Librarian has a single supported query pipeline. Implementation details
live in ``query_v2``; this stable name keeps API and worker imports simple.
"""

from app.jobs.librarian.query_v2 import LibrarianQueryV2


class LibrarianOrchestrator(LibrarianQueryV2):
    """Stable application-facing name for the active Librarian pipeline."""
