"""Pydantic schemas for Novelist job."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NovelistRunCreate(BaseModel):
    """Payload to start a simplified novelist draft job."""

    unstructured_text: str = Field(
        ...,
        min_length=1,
        description="Raw unstructured text to be expanded into a chapter",
    )
    language: Optional[str] = Field(None, description="Target language")
    instructions: Optional[str] = Field(
        None, description="Extra parsing/writing instructions for the novelist"
    )
    previous_session_id: Optional[str] = Field(
        None,
        description=(
            "Optional EntityInstance ID used to fetch previous session context for continuity."
        ),
    )
    # Internal runtime field, resolved server-side from previous_session_id.
    previous_session_text: Optional[str] = Field(
        None,
        description="Resolved previous session text used only as non-authoritative continuity context.",
    )
    # Internal runtime field, generated server-side for continuity prompting.
    previous_session_summary: Optional[str] = Field(
        None,
        description="Resolved continuity summary used as non-authoritative context.",
    )


class NovelistRunRead(BaseModel):
    """Response representing a Novelist run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    background_job_id: Optional[int] = None
    ontology_id: Optional[int] = None
    ontology_instance_id: Optional[str] = None
    status: str
    stage: str
    settings: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    previous_session_id: Optional[str] = None
    previous_session_summary: Optional[str] = None
    previous_session_lookup_status: Optional[str] = None
    elder_qna_by_part: Optional[dict[str, dict[str, list[str]]]] = None
    scene_results: Optional[list[dict[str, Any]]] = None
    step_outputs: Optional[dict[str, Any]] = None
    timing_summary: Optional[dict[str, Any]] = None
    stage_timings: Optional[dict[str, float]] = None
    scene_progress: Optional[dict[str, dict[str, Any]]] = None
    draft_text: Optional[str] = None
    critic_notes: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _inject_continuity_fields(cls, data: Any) -> Any:
        def _extract_elder_qna(
            artifacts_payload: Any,
        ) -> Optional[dict[str, dict[str, list[str]]]]:
            if not isinstance(artifacts_payload, dict):
                return None
            elder_step = artifacts_payload.get("step_1_5_elder_query_planning")
            if not isinstance(elder_step, dict):
                return None
            per_part = elder_step.get("per_part")
            if not isinstance(per_part, dict):
                return None
            normalized: dict[str, dict[str, list[str]]] = {}
            for part_key in ("part_1", "part_2", "part_3"):
                part_data = per_part.get(part_key)
                part_payload = part_data if isinstance(part_data, dict) else {}
                queries_raw = part_payload.get("queries", [])
                context_raw = part_payload.get("elder_context", [])
                queries = [
                    str(item).strip()
                    for item in (queries_raw if isinstance(queries_raw, list) else [])
                    if str(item).strip()
                ][:5]
                elder_context = [
                    str(item).strip()
                    for item in (context_raw if isinstance(context_raw, list) else [])
                    if str(item).strip()
                ][:8]
                normalized[part_key] = {
                    "queries": queries,
                    "elder_context": elder_context,
                }
            return normalized

        if isinstance(data, dict):
            payload = data.get("request_payload") or {}
            artifacts = data.get("artifacts") or {}
            inputs = artifacts.get("inputs") if isinstance(artifacts, dict) else {}
            stages = artifacts.get("stages") if isinstance(artifacts, dict) else {}
            timings = artifacts.get("timings_ms") if isinstance(artifacts, dict) else {}
            step_outputs = artifacts.get("step_outputs") if isinstance(artifacts, dict) else None
            if not isinstance(payload, dict):
                payload = {}
            if not isinstance(inputs, dict):
                inputs = {}
            if not isinstance(stages, dict):
                stages = {}
            if not isinstance(timings, dict):
                timings = {}
            if not isinstance(step_outputs, dict):
                step_outputs = None

            scene_results = None
            revision_stage = stages.get("revision")
            if isinstance(revision_stage, dict) and isinstance(
                revision_stage.get("scenes"), list
            ):
                scene_results = revision_stage.get("scenes")

            timing_summary = None
            if timings:
                total_ms = timings.get("total")
                timing_summary = {
                    "total_ms": float(total_ms) if isinstance(total_ms, (int, float)) else 0.0,
                    "by_stage_ms": timings,
                    "scene_count": len(scene_results or []),
                }
            if data.get("previous_session_id") is None:
                data["previous_session_id"] = (
                    payload.get("previous_session_id") or inputs.get("previous_session_id")
                )
            if data.get("previous_session_summary") is None:
                data["previous_session_summary"] = (
                    inputs.get("previous_session_summary")
                    or inputs.get("continuity_brief")
                    or payload.get("previous_session_summary")
                )
            if data.get("previous_session_lookup_status") is None:
                data["previous_session_lookup_status"] = inputs.get(
                    "previous_session_lookup_status"
                )
            if data.get("elder_qna_by_part") is None:
                data["elder_qna_by_part"] = _extract_elder_qna(artifacts)
            if data.get("scene_results") is None:
                data["scene_results"] = scene_results
            if data.get("step_outputs") is None:
                data["step_outputs"] = step_outputs
            if data.get("timing_summary") is None:
                data["timing_summary"] = timing_summary
            if data.get("stage_timings") is None:
                data["stage_timings"] = timings or None
            if data.get("scene_progress") is None and isinstance(artifacts, dict):
                scene_progress = artifacts.get("scene_progress")
                data["scene_progress"] = (
                    scene_progress if isinstance(scene_progress, dict) else None
                )
            return data

        payload = getattr(data, "request_payload", None) or {}
        artifacts = getattr(data, "artifacts", None) or {}
        inputs = artifacts.get("inputs") if isinstance(artifacts, dict) else {}
        stages = artifacts.get("stages") if isinstance(artifacts, dict) else {}
        timings = artifacts.get("timings_ms") if isinstance(artifacts, dict) else {}
        step_outputs = artifacts.get("step_outputs") if isinstance(artifacts, dict) else None
        previous_session_id = None
        previous_session_summary = None
        previous_session_lookup_status = None
        if isinstance(payload, dict):
            previous_session_id = payload.get("previous_session_id")
            previous_session_summary = payload.get("previous_session_summary")
        if isinstance(inputs, dict):
            previous_session_id = previous_session_id or inputs.get("previous_session_id")
            previous_session_summary = (
                previous_session_summary
                or inputs.get("previous_session_summary")
                or inputs.get("continuity_brief")
            )
            previous_session_lookup_status = inputs.get("previous_session_lookup_status")

        scene_results = None
        if isinstance(stages, dict):
            revision_stage = stages.get("revision")
            if isinstance(revision_stage, dict) and isinstance(
                revision_stage.get("scenes"), list
            ):
                scene_results = revision_stage.get("scenes")

        timing_summary = None
        if isinstance(timings, dict) and timings:
            total_ms = timings.get("total")
            timing_summary = {
                "total_ms": float(total_ms) if isinstance(total_ms, (int, float)) else 0.0,
                "by_stage_ms": timings,
                "scene_count": len(scene_results or []),
            }
        return {
            "id": getattr(data, "id"),
            "agent_id": getattr(data, "agent_id"),
            "background_job_id": getattr(data, "background_job_id", None),
            "ontology_id": getattr(data, "ontology_id", None),
            "ontology_instance_id": getattr(data, "ontology_instance_id", None),
            "status": getattr(data, "status"),
            "stage": getattr(data, "stage"),
            "settings": getattr(data, "settings", None),
            "request_payload": payload if isinstance(payload, dict) else None,
            "artifacts": artifacts if isinstance(artifacts, dict) else None,
            "previous_session_id": previous_session_id,
            "previous_session_summary": previous_session_summary,
            "previous_session_lookup_status": previous_session_lookup_status,
            "elder_qna_by_part": _extract_elder_qna(artifacts),
            "scene_results": scene_results,
            "step_outputs": step_outputs if isinstance(step_outputs, dict) else None,
            "timing_summary": timing_summary,
            "stage_timings": timings if isinstance(timings, dict) and timings else None,
            "scene_progress": (
                artifacts.get("scene_progress")
                if isinstance(artifacts, dict)
                and isinstance(artifacts.get("scene_progress"), dict)
                else None
            ),
            "draft_text": getattr(data, "draft_text", None),
            "critic_notes": getattr(data, "critic_notes", None),
            "error_message": getattr(data, "error_message", None),
            "created_at": getattr(data, "created_at"),
            "updated_at": getattr(data, "updated_at"),
        }
