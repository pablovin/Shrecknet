from . import crud_characteristic
from . import crud_concept
from . import crud_gameworld
from . import crud_import_export
from . import crud_page
from . import crud_page_links_update
from . import crud_users
from . import crud_agent
from . import crud_chat_history
from . import crud_specialist_source
from . import crud_specialist_vectordb
from . import crud_specialist_agent
from . import crud_library_item
from . import crud_library_vectordb
from . import crud_agent_library_item
try:
    from . import crud_vectordb
except Exception:  # pragma: no cover - optional dependency
    class _DummyVectorDB:
        def query_world(self, *args, **kwargs):
            return []

    crud_vectordb = _DummyVectorDB()

__all__ = [
    "crud_characteristic",
    "crud_concept",
    "crud_gameworld",
    "crud_import_export",
    "crud_page",
    "crud_page_links_update",
    "crud_users",
    "crud_agent",
    "crud_chat_history",
    "crud_specialist_source",
    "crud_specialist_vectordb",
    "crud_specialist_agent",
    "crud_library_item",
    "crud_library_vectordb",
    "crud_agent_library_item",
]
if crud_vectordb:
    __all__.append("crud_vectordb")
