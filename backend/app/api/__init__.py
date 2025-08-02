modules = []
for _mod in [
    "api_characteristic",
    "api_concept",
    "api_page",
    "api_user",
    "api_gameworld",
    "api_import_export",
    "api_vectordb",
    "api_agent",
    "api_specialist",
    "api_backup",
    "api_library",
    "api_jobs",
    "api_user_note",
    "api_embedding",
    "api_news",
]:
    try:
        globals()[_mod] = __import__(f"app.api.{_mod}", fromlist=["router"])
        modules.append(_mod)
    except Exception:
        from fastapi import APIRouter
        import types
        globals()[_mod] = types.SimpleNamespace(router=APIRouter())

__all__ = modules
