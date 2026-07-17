"""Stable application entrypoint for the default Elder v2 pipeline."""

from app.jobs.elder.query_v2 import ElderQueryV2


class ElderOrchestrator(ElderQueryV2):
    """Application-facing name for Elder query and retrieval v2."""
