# Backend_2 - Shrecknet Primary Backend

This is the primary FastAPI backend service for Shrecknet.

## Documentation

The main backend documentation has been moved to the centralized Documentation folder:

📖 **[Backend Documentation](../Documentation/Backend/README.md)**

## Quick Links

- [Complete Backend Documentation](../Documentation/Backend/README.md)
- [API Reference](../Documentation/API/)
- [Architecture Documentation](../Documentation/Architecture/)
- [Database Setup](../Documentation/Database/)

## Local Development

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[ml,test]"

# Run the server
uvicorn app.main:app --reload

# Run tests
pytest
```

For complete deployment instructions, see the [Deployment Documentation](../Documentation/Deployment/).
