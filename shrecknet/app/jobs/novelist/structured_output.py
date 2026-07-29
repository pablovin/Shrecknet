"""Strict structured-output contracts for the active Novelist pipeline."""

from __future__ import annotations

from app.integrations.llm.structured_output import strict_json_schema


RETRIEVAL_QUESTIONS_RESPONSE_FORMAT = strict_json_schema(
    "novelist_retrieval_questions",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["questions"],
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
            }
        },
    },
)

CONTEXT_RESPONSE_FORMAT = strict_json_schema(
    "novelist_scene_context",
    {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "prior_events",
            "relationship_summaries",
            "personality_reminders",
            "unresolved_tensions",
            "style_details",
            "contradiction_warnings",
        ],
        "properties": {
            "prior_events": {"type": "string"},
            "relationship_summaries": {"type": "string"},
            "personality_reminders": {"type": "string"},
            "unresolved_tensions": {"type": "string"},
            "style_details": {"type": "string"},
            "contradiction_warnings": {"type": "string"},
        },
    },
)

_CRITIC_LIST = {"type": "array", "items": {"type": "string"}}
CRITIC_RESPONSE_FORMAT = strict_json_schema(
    "novelist_chapter_critic",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["global_notes", "scenes"],
        "properties": {
            "global_notes": _CRITIC_LIST,
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "scene_name",
                        "continuity_issues",
                        "duplication",
                        "missing_transitions",
                        "voice_drift",
                        "pacing",
                        "graph_contradictions",
                        "exposition_problems",
                    ],
                    "properties": {
                        "scene_name": {"type": "string"},
                        "continuity_issues": _CRITIC_LIST,
                        "duplication": _CRITIC_LIST,
                        "missing_transitions": _CRITIC_LIST,
                        "voice_drift": _CRITIC_LIST,
                        "pacing": _CRITIC_LIST,
                        "graph_contradictions": _CRITIC_LIST,
                        "exposition_problems": _CRITIC_LIST,
                    },
                },
            },
        },
    },
)
