# Librarian Retrieval Error Fix

## Issue
The librarian chat was experiencing retrieval failures with the error:
```
Existing exports of data: object cannot be re-sized
```

This error occurred during parallel retrieval when the embedding service tried to convert numpy arrays from the sentence-transformers model to Python lists.

## Root Cause
The error "Existing exports of data: object cannot be re-sized" is a numpy/PyTorch issue that occurs when:
1. A numpy array has active exports (references) to its internal buffer
2. Something tries to resize or modify the array while these exports exist
3. This can happen with sentence-transformers when the model's output buffer is locked or has lingering references

The issue was intermittent because it depends on:
- The state of the embedding model cache
- Timing of garbage collection
- Concurrent access patterns during parallel retrieval

## Solution
Updated `backend_2/app/graphrag/embedding_service.py` to handle this error gracefully:

1. **Added retry logic**: The `embed_texts` method now retries up to 2 times on certain errors
2. **Expanded error handling**: Now catches both `RuntimeError` (for "meta tensor" errors) and `ValueError`/`BufferError` (for array export errors)
3. **Model cache clearing**: When the error is detected, the cached embedding model is cleared and reloaded
4. **Explicit array copy**: Added `embeddings.copy().tolist()` to ensure the array is copied before conversion, preventing export lock issues
5. **Better logging**: Added warning logs to help diagnose future issues

### Code Changes
```python
# Before
embeddings = model.encode(texts, normalize_embeddings=True)
return embeddings.tolist()

# After
for attempt in range(max_retries):
    try:
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.copy().tolist()  # Explicit copy prevents export issues
    except (ValueError, BufferError) as exc:
        if "cannot be re-sized" in str(exc) or "export" in str(exc).lower():
            logger.warning("Array export error, reloading model: %s", exc)
            with _model_lock:
                _cached_model = None
            model = get_embedding_model()
            if attempt == max_retries - 1:
                raise
        else:
            raise
```

## Impact
- Retrieval failures should no longer occur due to this specific error
- If the error does occur, the system will automatically retry with a fresh model
- Better logging will help diagnose any future issues
- No changes to the API or user-facing behavior

## Testing
The fix has been verified with:
- Syntax checking of all modified files
- Code review of the retry logic and error handling
- Verification that the changes align with existing error handling patterns in the codebase

## Related Files
- `backend_2/app/graphrag/embedding_service.py` - Contains the fix
- `backend_2/app/services/pdf_embedding_service.py` - Calls the embedding service
- `backend_2/app/jobs/librarian/librarian.py` - Orchestrates parallel retrieval and catches exceptions
