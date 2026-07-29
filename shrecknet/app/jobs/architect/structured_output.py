"""Structured-output contracts and compatibility handling for Architect LLM calls."""

from __future__ import annotations

from typing import Any

from app.integrations.llm.structured_output import (
    chat_with_structured_output,
    strict_json_schema,
)


SCENE_SEGMENTATION_RESPONSE_FORMAT = strict_json_schema(
    "architect_scene_segmentation",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "scene_id",
                        "name",
                        "description",
                        "start_paragraph",
                        "end_paragraph",
                    ],
                    "properties": {
                        "scene_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "start_paragraph": {"type": "integer"},
                        "end_paragraph": {"type": "integer"},
                    },
                },
            }
        },
    },
)

ENTITY_EXTRACTION_RESPONSE_FORMAT = strict_json_schema(
    "architect_entity_extraction",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["scene_ref", "entities"],
                    "properties": {
                        "scene_ref": {"type": "string"},
                        "entities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "name",
                                    "ontology",
                                    "status",
                                    "matched_alias",
                                    "confidence",
                                    "why",
                                ],
                                "properties": {
                                    "name": {"type": "string"},
                                    "ontology": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["existing", "new"],
                                    },
                                    "matched_alias": {
                                        "type": ["string", "null"],
                                    },
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "why": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
)

MILESTONE_EXTRACTION_RESPONSE_FORMAT = strict_json_schema(
    "architect_milestone_extraction",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["scene_ref", "milestones"],
                    "properties": {
                        "scene_ref": {"type": "string"},
                        "milestones": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "title",
                                    "description",
                                    "boundary_type",
                                    "adjacent_to",
                                    "related_to",
                                ],
                                "properties": {
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                    "boundary_type": {
                                        "type": "string",
                                        "enum": ["begin", "end", "none"],
                                    },
                                    "adjacent_to": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "related_to": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": [
                                                "entity",
                                                "relationship_label",
                                                "relationship_description",
                                            ],
                                            "properties": {
                                                "entity": {"type": "string"},
                                                "relationship_label": {"type": "string"},
                                                "relationship_description": {
                                                    "type": "string"
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        },
    },
)

SCENE_MERGE_RESPONSE_FORMAT = strict_json_schema(
    "architect_scene_merge",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "scene_ref",
                        "name",
                        "description",
                        "source_scene_refs",
                    ],
                    "properties": {
                        "scene_ref": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "source_scene_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            }
        },
    },
)

