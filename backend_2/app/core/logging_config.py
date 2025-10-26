from __future__ import annotations

import logging
from logging.config import dictConfig


def configure_logging() -> None:
    """Configure application-wide logging if not already initialised."""

    if logging.getLogger().handlers:
        return

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default",}
            },
            "loggers": {
                "backend_2": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
            "root": {"handlers": ["console"], "level": "INFO",},
        }
    )
