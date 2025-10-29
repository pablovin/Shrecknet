# Neo4j Driver Event Loop Fix

## Problem

When running Celery tasks that use async Neo4j operations, we encountered a runtime error:

```
RuntimeError: Task <Task pending name='Task-30' coro=<_embed_ontology_impl() ...> got Future <Future pending> attached to a different loop
```

This error occurred because:

1. The Neo4j `AsyncDriver` was created as a singleton in `get_driver()`
2. Celery workers use `asyncio.run()` to execute async code, which creates a new event loop for each task
3. The async driver's internal operations were bound to the original event loop
4. When a new event loop tried to use the driver, the driver's futures/tasks were still attached to the old loop

## Solution

We modified `app/graph/neo4j.py` to make `get_driver()` event-loop-aware:

### Changes

1. **Track the event loop**: Added `_driver_loop` to track which event loop the driver was created in
2. **Detect loop changes**: Check if the current event loop is different from the one the driver was created in
3. **Recreate driver**: When switching to a new event loop, discard the old driver and create a new one

### Code

```python
_driver: AsyncDriver | None = None
_driver_loop: asyncio.AbstractEventLoop | None = None

def get_driver() -> AsyncDriver:
    """Get the Neo4j async driver, ensuring it's bound to the current event loop."""
    global _driver, _driver_loop
    
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    
    # Recreate driver if loop changed
    if _driver is None or (_driver_loop is not None and current_loop is not _driver_loop):
        if _driver is not None and _driver_loop is not None and current_loop is not _driver_loop:
            _driver = None
            _driver_loop = None
        
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=3600,
        )
        _driver_loop = current_loop
    
    return _driver
```

## Benefits

1. **Celery tasks work correctly**: Each task execution gets a driver bound to its event loop
2. **No breaking changes**: API remains the same, existing code continues to work
3. **Efficient**: Driver is still reused within the same event loop (e.g., FastAPI requests)
4. **Automatic**: No changes needed to existing Celery tasks

## Testing

Added comprehensive tests in `tests/test_neo4j_driver_event_loop.py`:

- Test driver recreation across different event loops
- Test driver reuse within the same event loop
- Test the complete `run_async` + driver scenario (simulates Celery)
- Test driver creation without an event loop
- Test driver in async contexts (FastAPI endpoints)

## Related Files

- `app/graph/neo4j.py` - Main fix
- `app/utils/async_helpers.py` - Helper for running async code in Celery tasks
- `app/tasks/neo4j_embedding.py` - Celery task that triggered the issue
- `app/tasks/pdf_embedding.py` - Also uses driver in Celery tasks
- `app/tasks/ontology_links.py` - Also uses driver in Celery tasks

All these tasks now work correctly without "attached to different loop" errors.
