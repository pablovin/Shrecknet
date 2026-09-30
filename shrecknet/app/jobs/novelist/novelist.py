"""Backward-compatible import location for Novelist v3.

The former scene/critic/revision implementation was removed. New code should
import :class:`NovelistOrchestrator` from ``orchestrator`` directly.
"""

from app.jobs.novelist.orchestrator import NovelistOrchestrator

__all__ = ["NovelistOrchestrator"]
