from datetime import datetime
from typing import Any

def serialize_value(value: Any) -> Any:
    """Recursively convert datetime objects to ISO formatted strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    return value
