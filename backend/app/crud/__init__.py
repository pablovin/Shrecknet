from . import crud_characteristic
from . import crud_concept
from . import crud_gameworld
from . import crud_import_export
from . import crud_page
from . import crud_page_links_update
from . import crud_users
try:
    from . import crud_agent
except Exception:  # pragma: no cover - optional heavy dependency
    crud_agent = None
from . import crud_chat_history
try:
    from . import crud_specialist_source
except Exception:
    crud_specialist_source = None
try:
    from . import crud_specialist_vectordb
except Exception:
    crud_specialist_vectordb = None
try:
    from . import crud_specialist_agent
except Exception:
    crud_specialist_agent = None
from . import crud_library_item
try:
    from . import crud_library_vectordb
except Exception:
    crud_library_vectordb = None
from . import crud_agent_library_item
from . import crud_user_note
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
    "crud_chat_history",
    "crud_library_item",
    "crud_agent_library_item",
    "crud_user_note",
]
if crud_vectordb:
    __all__.append("crud_vectordb")
if crud_agent:
    __all__.append("crud_agent")
if crud_specialist_source:
    __all__.append("crud_specialist_source")
if crud_specialist_vectordb:
    __all__.append("crud_specialist_vectordb")
if crud_specialist_agent:
    __all__.append("crud_specialist_agent")
if crud_library_vectordb:
    __all__.append("crud_library_vectordb")
