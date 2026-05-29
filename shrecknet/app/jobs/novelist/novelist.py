"""Scene-centric Novelist orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.shrecknet import validate_or_repair_json
from app.jobs.architect.prompts import ARCHITECT_ENTITY_PROPOSAL_PROMPT
from app.jobs.architect.scene_centric_chunking import (
    build_scene_chunks,
    extract_paragraphs_from_sources,
    segment_chunk_into_scenes,
)
from app.jobs.architect.schemas import ChunkExtractionResponse
from app.jobs.novelist.prompts import (
    NOVELIST_STEP_2_RETRIEVAL_QUESTION_PLANNER_PROMPT,
    NOVELIST_STEP_4_CONTEXT_BUILD_PROMPT,
    NOVELIST_STEP_5_DRAFT_PROMPT,
    NOVELIST_STEP_6_CRITIC_PROMPT,
    NOVELIST_STEP_7_FINAL_REWRITE_PROMPT,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

logger = logging.getLogger(__name__)
SCENE_PIPELINE_BATCH_SIZE_DEFAULT = 10

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]
ElderQueryRunner = Callable[[Agent, str], Awaitable[list[dict[str, Any]]]]
ArchitectScaffoldingRunner = Callable[
    [Agent, str, str, str | None], Awaitable[dict[str, Any]]
]


class NovelistOrchestrator:
    """Scene-centric Novelist pipeline.

    Stages:
    ingest -> scaffolding -> scene_package -> retrieval -> intent_drafting ->
    prose_generation -> critic -> revision -> merging -> done
    """

    def __init__(
        self,
        *,
        llm_client: ShreckLLMClient,
        model_policy: ModelPolicy,
        max_concurrency: int = 10,
        scene_pipeline_batch_size: int = SCENE_PIPELINE_BATCH_SIZE_DEFAULT,
        elder_query_concurrency: int = 1,
        elder_query_timeout_s: float = 75.0,
        elder_query_runner: ElderQueryRunner | None = None,
        architect_scaffolding_runner: ArchitectScaffoldingRunner | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.novelist_planning_model = getattr(model_policy, "model_novelist_planning", None)
        self.novelist_prose_model = getattr(model_policy, "model_novelist_prose", None)
        self.novelist_critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.repair_json_model = getattr(model_policy, "model_agents_repair_json", None)
        self.max_concurrency = max(1, min(10, max_concurrency))
        self.scene_pipeline_batch_size = max(1, min(50, int(scene_pipeline_batch_size)))
        self._elder_query_concurrency = max(1, int(elder_query_concurrency))
        self._elder_query_timeout_s = max(1.0, float(elder_query_timeout_s))
        self._elder_query_semaphore = asyncio.Semaphore(self._elder_query_concurrency)
        self._scene_prose_max_chars = 1400
        self._critic_input_max_chars = 110_000
        self._revision_input_max_chars = 110_000
        self.elder_query_runner = elder_query_runner
        self.architect_scaffolding_runner = architect_scaffolding_runner
        self._debug_step_label: str | None = None
        self._debug_entity_name: str = "unknown_entity"
        self._debug_run_date: str = "unknown_run_date"
        self._debug_prompt_calls: list[dict[str, Any]] = []
        self._debug_response_calls: list[dict[str, Any]] = []
        self._debug_output_dir: Path | None = None
        self._model_step_2_4 = (
            self.novelist_planning_model
            or self.model_policy.get_model(LLMTask.SYNTHESIS)
        )
        self._model_step_5_7 = (
            self.novelist_prose_model
            or self.model_policy.get_model(LLMTask.SYNTHESIS)
        )
        self._model_step_6 = (
            self.novelist_critic_model
            or self.model_policy.get_model(LLMTask.SYNTHESIS)
        )

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        conversation_id: str | None = None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        started_total = time.monotonic()
        unstructured_text = payload.unstructured_text.strip()
        language = (payload.language or "").strip()
        instructions = (payload.instructions or "").strip()
        self._debug_entity_name = self._resolve_debug_entity_name(
            agent_name=getattr(agent, "name", None),
            previous_session_id=payload.previous_session_id,
        )
        self._debug_run_date = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        artifacts: dict[str, Any] = {
            "inputs": {
                "unstructured_text": payload.unstructured_text,
                "language": payload.language,
                "instructions": payload.instructions,
                "previous_session_id": payload.previous_session_id,
            },
            "stages": {},
            "scene_progress": {},
            "timings_ms": {},
            "models": {},
        }

        if stage_callback:
            await stage_callback(
                NovelistStage.INGEST,
                {
                    "artifacts": artifacts,
                },
            )

        # Stage: scaffolding
        self._debug_step_label = "step_1"
        self._debug_prompt_calls = []
        self._debug_response_calls = []
        scaffolding_t0 = time.monotonic()
        scaffolding = await self._build_scaffolding(
            agent=agent,
            unstructured_text=unstructured_text,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        step_1_scenes = self._build_step_1_scenes(
            scaffolding.get("scenes", []),
            language_output_text=language,
            instructions=instructions,
        )
        if instructions and any(not str(scene.get("instructions") or "").strip() for scene in step_1_scenes):
            logger.warning(
                "novelist_scene_instructions_missing_after_fallback: run_conversation_id=%s",
                conversation_id,
            )
        scaffolding["scenes"] = step_1_scenes
        artifacts["stages"]["scaffolding"] = scaffolding
        artifacts["timings_ms"]["scaffolding"] = round(
            (time.monotonic() - scaffolding_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.SCAFFOLDING,
                {
                    "artifacts": artifacts,
                    "scene_count": len(scaffolding.get("scenes", [])),
                },
            )
        self._debug_entity_name = self._resolve_debug_entity_name_from_scaffolding(
            scaffolding=scaffolding,
            fallback=self._debug_entity_name,
        )

        self._write_step_debug_files(
            step="step_1",
            prompt_payload={
                "entity_name": self._debug_entity_name,
                "step": "step_1",
                "llm_calls": self._debug_prompt_calls,
            },
            response_payload={
                "entity_name": self._debug_entity_name,
                "step": "step_1",
                "llm_calls": self._debug_response_calls,
                "final_step_response": {"scenes": step_1_scenes},
            },
        )
        self._debug_step_label = None

        return await self._execute_v2(
            agent=agent,
            step_1_scenes=step_1_scenes,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
            stage_callback=stage_callback,
            artifacts=artifacts,
            started_total=started_total,
        )

    @staticmethod
    def _sanitize_debug_component(value: str | None) -> str:
        compact = re.sub(r"\s+", "_", str(value or "").strip().lower())
        compact = re.sub(r"[^a-z0-9._-]", "_", compact)
        compact = compact.strip("._-")
        return compact or "unknown_entity"

    def _resolve_debug_entity_name(
        self,
        *,
        agent_name: str | None,
        previous_session_id: str | None,
    ) -> str:
        if agent_name and str(agent_name).strip():
            return self._sanitize_debug_component(agent_name)
        if previous_session_id and str(previous_session_id).strip():
            return self._sanitize_debug_component(previous_session_id)
        return "unknown_entity"

    def _resolve_debug_entity_name_from_scaffolding(
        self,
        *,
        scaffolding: dict[str, Any],
        fallback: str,
    ) -> str:
        scenes = scaffolding.get("scenes", []) if isinstance(scaffolding, dict) else []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            entities = scene.get("related_entities", [])
            if isinstance(entities, list):
                for entity in entities:
                    if isinstance(entity, dict):
                        candidate = str(
                            entity.get("entity")
                            or entity.get("name")
                            or entity.get("alias")
                            or ""
                        ).strip()
                    else:
                        candidate = str(entity or "").strip()
                    if candidate:
                        return self._sanitize_debug_component(candidate)
            name = str(scene.get("name") or "").strip()
            if name:
                return self._sanitize_debug_component(name)
        return self._sanitize_debug_component(fallback)

    def _build_step_6_7_conversation_id(self, conversation_id: str | None) -> str:
        base = str(conversation_id or "").strip()
        if base:
            return f"{base}:step6_7"
        return f"novelist_step6_7:{self._debug_run_date}:{self._debug_entity_name}"

    def _build_scene_4_5_conversation_id(
        self,
        *,
        base_conversation_id: str | None,
        scene_id: str,
    ) -> str:
        base = str(base_conversation_id or "").strip()
        scene = str(scene_id or "scene").strip() or "scene"
        scene = re.sub(r"[^a-zA-Z0-9_.:-]", "_", scene)
        if base:
            return f"{base}:step4_5:{scene}"
        return f"novelist_step4_5:{self._debug_run_date}:{self._debug_entity_name}:{scene}"

    def _write_step_debug_files(
        self,
        *,
        step: str,
        prompt_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> None:
        # File-based debug dumps intentionally disabled; keep job-level artifacts only.
        return

    def _resolve_local_tests_output_dir(self, *, run_date: str) -> Path:
        if self._debug_output_dir is not None:
            return self._debug_output_dir
        data_root = os.getenv("SHRECKNET_DATA_DIR", "/data")
        module_root = Path(__file__).resolve().parents[3]
        candidates = [
            Path(data_root) / "local_test" / "architect" / "draft" / run_date,
            Path(data_root) / "local_tests" / "architect" / "draft" / run_date,
            module_root / "databases" / "local_test" / "architect" / "draft" / run_date,
            module_root / "databases" / "local_tests" / "architect" / "draft" / run_date,
            Path.cwd()
            / "shrecknet"
            / "databases"
            / "local_test"
            / "architect"
            / "draft"
            / run_date,
            Path.cwd() / "databases" / "local_test" / "architect" / "draft" / run_date,
            Path.cwd() / "local_test" / "architect" / "draft" / run_date,
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                self._debug_output_dir = candidate
                logger.info("novelist_debug_output_dir_resolved: path=%s", candidate)
                return candidate
            except OSError:
                continue
        fallback = Path("local_test") / "architect" / "draft" / run_date
        fallback.mkdir(parents=True, exist_ok=True)
        self._debug_output_dir = fallback
        logger.info("novelist_debug_output_dir_resolved: path=%s", fallback)
        return fallback

    async def _process_scene_pipeline(
        self,
        *,
        agent: Agent,
        scene: dict[str, Any],
        index: int,
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], str, dict[str, Any], list[dict[str, Any]]]:
        raise RuntimeError("V1 novelist scene pipeline has been removed")

        step_3_t0 = time.monotonic()
        try:
            step_3_scene_packages, retrieval_by_scene, step_3_traces = await self._collect_scene_retrieval(
                agent=agent,
                scene_packages=[step_2_package],
                language=language,
                instructions=instructions,
                conversation_id=scene_conversation_id,
                use_conversation_memory=True,
                debug_collector=step_3_calls,
                use_delta_prompt=True,
            )
            step_3_package = step_3_scene_packages[0]
            retrieval_payload = retrieval_by_scene.get(scene_id, {})
            elder_trace_rows = [row for row in step_3_traces if str(row.get("scene_id")) == scene_id]
            step_3_status = "ok"
            step_3_error = None
        except Exception as exc:
            logger.warning("scene_pipeline_step3_failed scene_id=%s error=%s", scene_id, exc, exc_info=True)
            step_3_package = dict(step_2_package)
            retrieval_payload = {}
            elder_trace_rows = []
            step_3_status = "error"
            step_3_error = str(exc)

        step_3_latency_ms = round((time.monotonic() - step_3_t0) * 1000, 2)
        step_3_trace: list[dict[str, Any]] = [
            {
                "scene_id": scene_id,
                "conversation_id": scene_conversation_id,
                "status": step_3_status,
                "error": step_3_error,
                "timing_ms": step_3_latency_ms,
                "elder_query_traces": elder_trace_rows,
                "llm_calls": step_3_calls,
                "delta_output": self._scene_delta_allowlist(
                    step_2_package,
                    step_3_package,
                    allowed_fields=(
                        "prior_events",
                        "relationship_summaries",
                        "personality_reminders",
                        "unresolved_tensions",
                        "style_details",
                        "contradiction_warnings",
                        "queries",
                        "questions_answers",
                    ),
                ),
            }
        ]

        step_4_t0 = time.monotonic()
        step_4_delta_input = self._scene_delta_allowlist(
            step_2_package,
            step_3_package,
            allowed_fields=(
                "prior_events",
                "relationship_summaries",
                "personality_reminders",
                "unresolved_tensions",
                "style_details",
                "contradiction_warnings",
                "queries",
                "questions_answers",
            ),
        )
        step_4_intent = await self._draft_scene_intent_single(
            scene_id=scene_id,
            delta_input=step_4_delta_input,
            language=language,
            instructions=instructions,
            conversation_id=scene_conversation_id,
            debug_collector=step_4_calls,
        )
        step_4_package = {**step_3_package, **step_4_intent}
        step_4_latency_ms = round((time.monotonic() - step_4_t0) * 1000, 2)
        step_4_trace: dict[str, Any] = {
            "scene_id": scene_id,
            "conversation_id": scene_conversation_id,
            "status": "ok",
            "timing_ms": step_4_latency_ms,
            "llm_calls": step_4_calls,
            "delta_output": self._scene_delta_allowlist(
                step_3_package,
                step_4_package,
                allowed_fields=(
                    "what_happens",
                    "emotional_progression",
                    "speaking_goals",
                    "implied_history",
                    "forbidden_contradictions",
                ),
            ),
        }

        step_5_t0 = time.monotonic()
        step_5_delta_input = self._scene_delta_allowlist(
            step_3_package,
            step_4_package,
            allowed_fields=(
                "what_happens",
                "emotional_progression",
                "speaking_goals",
                "implied_history",
                "forbidden_contradictions",
            ),
        )
        step_5_paragraph = await self._generate_scene_paragraph_single(
            scene_id=scene_id,
            delta_input=step_5_delta_input,
            language=language,
            instructions=instructions,
            conversation_id=scene_conversation_id,
            debug_collector=step_5_calls,
        )
        step_5_latency_ms = round((time.monotonic() - step_5_t0) * 1000, 2)
        step_5_trace: list[dict[str, Any]] = [
            {
                "scene_id": scene_id,
                "conversation_id": scene_conversation_id,
                "status": "ok",
                "timing_ms": step_5_latency_ms,
                "llm_calls": step_5_calls,
                "delta_output": {"paragraph": {"before": "", "after": step_5_paragraph}},
            }
        ]
        return (
            index,
            step_2_package,
            step_3_package,
            retrieval_payload,
            step_2_trace,
            step_3_trace,
            step_4_intent,
            step_4_package,
            step_5_paragraph,
            step_4_trace,
            step_5_trace,
        )

    async def _build_scaffolding(
        self,
        *,
        agent: Agent,
        unstructured_text: str,
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        if self.architect_scaffolding_runner:
            try:
                shared = await self.architect_scaffolding_runner(
                    agent,
                    unstructured_text,
                    instructions,
                    conversation_id,
                )
                scenes = shared.get("scenes") if isinstance(shared, dict) else None
                if isinstance(scenes, list) and scenes:
                    return shared
                logger.warning(
                    "architect_scaffolding_runner returned empty/invalid scenes, falling back to local scaffolding"
                )
            except Exception:
                logger.warning(
                    "architect_scaffolding_runner_failed_fallback_to_local",
                    exc_info=True,
                )

        model = self._model_step_2_4
        paragraphs = extract_paragraphs_from_sources(unstructured_text, None)
        if not paragraphs:
            paragraphs = [re.sub(r"\s+", " ", unstructured_text).strip()][:1]

        chunks = build_scene_chunks(paragraphs, token_limit=16_000)
        segmented_scenes: list[dict[str, Any]] = []

        for chunk in chunks:
            try:
                scenes = await segment_chunk_into_scenes(
                    llm_client=self.llm_client,
                    model=model,
                    repair_model=self.repair_json_model or model,
                    marked_paragraphs=chunk.marked_paragraphs,
                    paragraph_count=chunk.paragraph_count,
                    paragraphs=chunk.paragraphs,
                )
            except Exception as exc:
                logger.warning("scene_segmentation_failed chunk=%s err=%s", chunk.chunk_index, exc)
                scenes = [
                    {
                        "scene_id": 0,
                        "name": f"Scene {chunk.chunk_index + 1}",
                        "description": "",
                        "start_paragraph": chunk.paragraph_start,
                        "end_paragraph": chunk.paragraph_end,
                        "text": "\n".join(
                            [
                                f"[P{idx}] {paragraph}"
                                for idx, paragraph in enumerate(
                                    chunk.paragraphs, start=chunk.paragraph_start
                                )
                            ]
                        ),
                    }
                ]

            for local_scene in scenes:
                abs_start = int(local_scene.get("start_paragraph", chunk.paragraph_start))
                abs_end = int(local_scene.get("end_paragraph", chunk.paragraph_end))
                segmented_scenes.append(
                    {
                        "scene_id": "",
                        "name": str(local_scene.get("name") or "").strip(),
                        "scene_summary": str(local_scene.get("description") or "").strip(),
                        "raw_scene_text": str(local_scene.get("text") or "").strip(),
                        "source_paragraphs": list(range(abs_start, abs_end + 1)),
                        "source_anchors": [f"P{abs_start}-P{abs_end}"],
                    }
                )

        segmented_scenes.sort(key=lambda item: item["source_paragraphs"][0])
        for idx, scene in enumerate(segmented_scenes, start=1):
            scene["scene_id"] = f"scene-{idx:03d}"
            if not scene.get("name"):
                scene["name"] = f"Scene {idx}"

        merged_scenes = segmented_scenes

        ontology_definitions = self._serialize_ontology_definitions(agent)
        enriched_scenes = await self._extract_scene_entities_only(
            scenes=merged_scenes,
            ontology_definitions=ontology_definitions,
            model=model,
            conversation_id=conversation_id,
        )
        by_id: dict[str, dict[str, Any]] = {scene["scene_id"]: scene for scene in enriched_scenes}
        final_scenes: list[dict[str, Any]] = []
        for idx, scene in enumerate(enriched_scenes, start=1):
            scene_id = str(scene.get("scene_id") or f"scene-{idx:03d}").strip()
            source = by_id.get(scene_id, {})
            final_scenes.append(
                {
                    "scene_id": scene_id or f"scene-{idx:03d}",
                    "name": str(scene.get("name") or source.get("name") or scene_id).strip()
                    or f"Scene {idx}",
                    "scene_summary": str(
                        scene.get("scene_summary") or source.get("scene_summary") or ""
                    ).strip(),
                    "milestones": self._normalize_text_list(
                        scene.get("milestones") or source.get("milestones") or [], max_items=8
                    ),
                    "related_entities": self._normalize_text_list(
                        scene.get("related_entities") or source.get("related_entities") or [],
                        max_items=12,
                    ),
                    "instructions": instructions,
                    "Language_output_text": language,
                    "source_rawtext": source.get("raw_scene_text", ""),
                }
            )

        return {
            "model": model,
            "scene_count": len(final_scenes),
            "scenes": final_scenes,
        }

    async def _extract_scene_entities_only(
        self,
        *,
        scenes: list[dict[str, Any]],
        ontology_definitions: str,
        model: str,
        conversation_id: str | None,
    ) -> list[dict[str, Any]]:
        out = [dict(scene) for scene in scenes]
        scene_by_id = {str(s.get("scene_id") or ""): s for s in out}
        # Architect-style batching: entities in 3-scene batches.
        for idx in range(0, len(out), 3):
            batch = out[idx : idx + 3]
            scenes_payload = [
                {
                    "scene_ref": str(scene.get("scene_id") or ""),
                    "scene_name": str(scene.get("name") or "Scene"),
                    "scene_description": str(scene.get("scene_summary") or ""),
                    "scene_text": str(scene.get("raw_scene_text") or ""),
                }
                for scene in batch
            ]
            prompt = ARCHITECT_ENTITY_PROPOSAL_PROMPT.format(
                ontology_definitions=ontology_definitions,
                existing_entities="[]",
                scenes_payload=json.dumps(scenes_payload, ensure_ascii=False),
                scene_name=scenes_payload[0]["scene_name"] if scenes_payload else "",
                scene_description=scenes_payload[0]["scene_description"] if scenes_payload else "",
                scene_text=scenes_payload[0]["scene_text"] if scenes_payload else "",
            )
            entity_raw, _, _ = await self._call_llm(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                conversation_id=conversation_id,
            )
            payload = await self._parse_json_object_checked(entity_raw) or {}
            for row in payload.get("scenes", []) if isinstance(payload.get("scenes"), list) else []:
                if not isinstance(row, dict):
                    continue
                scene_ref = str(row.get("scene_ref") or "")
                target = scene_by_id.get(scene_ref)
                if target is None:
                    continue
                target["related_entities"] = self._parse_architect_entities({"entities": row.get("entities", [])})

        for scene in out:
            scene.setdefault("related_entities", [])

        for scene in out:
            scene.setdefault("milestones", [])
        return out

    async def _build_scene_packages(
        self,
        *,
        scenes: list[dict[str, Any]],
        language: str,
        instructions: str,
        conversation_id: str | None,
        use_conversation_memory: bool = False,
        debug_collector: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        raise RuntimeError("V1 novelist scene packaging has been removed")

        async def _run(scene: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                user_prompt = json.dumps(
                    scene,
                    ensure_ascii=True,
                )
                raw, latency_ms, _ = await self._call_llm(
                    model=self._model_step_2_4,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    conversation_id=conversation_id,
                    use_conversation_memory=use_conversation_memory,
                    debug_collector=debug_collector,
                )
                parsed = await self._parse_json_object_checked(raw) or {}
                package = parsed if isinstance(parsed, dict) else {}
                package["scene_id"] = str(package.get("scene_id") or scene.get("scene_id"))
                package["prior_knowledge_needed"] = self._normalize_prior_knowledge_pairs(
                    package.get("prior_knowledge_needed"),
                    scene_name=str(scene.get("name") or ""),
                )
                package.setdefault("scene_tone", "")
                package.setdefault("scene_goal", scene.get("scene_summary", ""))
                package.setdefault("instructions", instructions)
                package["_raw_scene_package"] = raw
                package["_latency_ms"] = latency_ms
                return package

        out = await asyncio.gather(*[asyncio.create_task(_run(scene)) for scene in scenes])
        out.sort(key=lambda item: str(item.get("scene_id", "")))
        return out

    async def _collect_scene_retrieval(
        self,
        *,
        agent: Agent,
        scene_packages: list[dict[str, Any]],
        language: str,
        instructions: str,
        conversation_id: str | None,
        use_conversation_memory: bool = False,
        debug_collector: list[dict[str, Any]] | None = None,
        use_delta_prompt: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
        if not self.elder_query_runner:
            empty_by_scene = {
                str(scene.get("scene_id")): {
                    "queries": [],
                    "questions_answers": [],
                    "buckets": self._empty_retrieval_buckets(),
                    "raw_sources": [],
                }
                for scene in scene_packages
            }
            enhanced = [self._apply_retrieval_to_scene(scene=scene, retrieval={}) for scene in scene_packages]
            return enhanced, empty_by_scene, []

        query_results: list[tuple[str, list[str]]] = []
        for scene in scene_packages:
            scene_id = str(scene.get("scene_id") or "")
            prior_knowledge_pairs = self._normalize_prior_knowledge_pairs(
                scene.get("prior_knowledge_needed"),
                scene_name=str(scene.get("name") or ""),
            )
            queries = self._questions_from_prior_knowledge_pairs(
                prior_knowledge_pairs,
                fallback=scene.get("open_questions_for_retrieval") or [],
            )
            queries = queries[:3]
            query_results.append((scene_id, queries))

        semaphore = asyncio.Semaphore(self.max_concurrency)
        question_traces: list[dict[str, Any]] = []

        async def _fetch(scene_id: str, query: str) -> tuple[str, str, list[dict[str, Any]]]:
            async with semaphore:
                started = time.monotonic()
                queued_at = time.monotonic()
                timeout_reason = ""
                try:
                    async with self._elder_query_semaphore:
                        entered_runner_at = time.monotonic()
                        raw_sources = await asyncio.wait_for(
                            self.elder_query_runner(agent, query),
                            timeout=self._elder_query_timeout_s,
                        )
                except TimeoutError:
                    entered_runner_at = time.monotonic()
                    timeout_reason = "timeout"
                    logger.warning(
                        "scene_retrieval_timeout scene=%s query=%s timeout_s=%s elder_concurrency=%d",
                        scene_id,
                        query,
                        self._elder_query_timeout_s,
                        self._elder_query_concurrency,
                    )
                    raw_sources = []
                except Exception:
                    entered_runner_at = time.monotonic()
                    timeout_reason = "error"
                    logger.warning("scene_retrieval_failed scene=%s query=%s", scene_id, query, exc_info=True)
                    raw_sources = []
                valid_sources = [s for s in raw_sources if isinstance(s, dict)]
                compact_answers = [
                    self._compact_retrieved_text(str(source.get("text") or ""), max_chars=220)
                    for source in valid_sources[:4]
                ]
                compact_answers = [item for item in compact_answers if item]
                question_traces.append(
                    {
                        "scene_id": scene_id,
                        "query": query,
                        "mode": "context",
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                        "queue_wait_ms": round((entered_runner_at - queued_at) * 1000, 2),
                        "retrieval_total_ms": round((time.monotonic() - started) * 1000, 2),
                        "timeout_s": self._elder_query_timeout_s,
                        "fallback_reason": timeout_reason or None,
                        "source_count": len(valid_sources),
                        "answer_text": " ".join(compact_answers[:3]).strip(),
                    }
                )
                return scene_id, query, valid_sources

        fetch_tasks = [
            asyncio.create_task(_fetch(scene_id, query))
            for scene_id, queries in query_results
            for query in queries
        ]
        fetched = await asyncio.gather(*fetch_tasks) if fetch_tasks else []

        grouped: dict[str, dict[str, Any]] = {
            scene_id: {
                "queries": queries,
                "questions_answers": [],
                "raw_sources": [],
                "buckets": self._empty_retrieval_buckets(),
            }
            for scene_id, queries in query_results
        }

        for scene_id, query, sources in fetched:
            grouped.setdefault(
                scene_id,
                {
                    "queries": [],
                    "questions_answers": [],
                    "raw_sources": [],
                    "buckets": self._empty_retrieval_buckets(),
                },
            )
            grouped[scene_id]["raw_sources"].extend(sources)
            compact_answers = [
                self._compact_retrieved_text(str(source.get("text") or ""), max_chars=220)
                for source in sources[:4]
            ]
            compact_answers = [item for item in compact_answers if item]
            grouped[scene_id]["questions_answers"].append(
                {
                    "question": query,
                    "answer": " ".join(compact_answers[:3]).strip(),
                }
            )
            grouped[scene_id]["instructions"] = instructions

        context_system = self._compose_system_prompt(
            NOVELIST_STEP_4_CONTEXT_BUILD_PROMPT,
            language=language,
            instructions=instructions,
        )
        context_sem = asyncio.Semaphore(self.max_concurrency)

        async def _build_context(scene: dict[str, Any]) -> tuple[str, dict[str, list[str]]]:
            scene_id = str(scene.get("scene_id") or "")
            retrieval_payload = grouped.get(scene_id, {})
            if use_delta_prompt:
                user_payload = json.dumps(
                    {
                        "scene_id": scene_id,
                        "Language_output_text": str(scene.get("Language_output_text") or language),
                        "questions_answers": retrieval_payload.get("questions_answers", []),
                        "queries": retrieval_payload.get("queries", []),
                    },
                    ensure_ascii=True,
                )
            else:
                user_payload = json.dumps(
                    {
                        "scene_id": scene_id,
                        "Language_output_text": str(scene.get("Language_output_text") or language),
                        "scene_payload": scene,
                        "questions_answers": retrieval_payload.get("questions_answers", []),
                    },
                    ensure_ascii=True,
                )
            async with context_sem:
                raw, _, _ = await self._call_llm(
                    model=self._model_step_2_4,
                    messages=[
                        {"role": "system", "content": context_system},
                        {"role": "user", "content": user_payload},
                    ],
                    temperature=0.1,
                    conversation_id=conversation_id,
                    use_conversation_memory=use_conversation_memory,
                    debug_collector=debug_collector,
                )
            payload = await self._parse_json_object_checked(raw) or {}
            if not isinstance(payload, dict):
                payload = {}
            buckets: dict[str, list[str]] = {}
            for key in self._empty_retrieval_buckets().keys():
                text = str(payload.get(key) or "").strip()
                buckets[key] = [text] if text else []
            return scene_id, buckets

        contexts = await asyncio.gather(
            *[asyncio.create_task(_build_context(scene)) for scene in scene_packages]
        )
        enhanced_scene_packages: list[dict[str, Any]] = []
        for scene_id, buckets in contexts:
            grouped.setdefault(scene_id, {"queries": [], "questions_answers": [], "raw_sources": []})
            grouped[scene_id]["buckets"] = buckets
            grouped[scene_id]["bucket_counts"] = {key: len(values) for key, values in buckets.items()}
            grouped[scene_id]["instructions"] = instructions

        retrieval_by_scene = grouped
        for scene in scene_packages:
            scene_id = str(scene.get("scene_id") or "")
            enhanced_scene_packages.append(
                self._apply_retrieval_to_scene(
                    scene=scene,
                    retrieval=retrieval_by_scene.get(scene_id, {}),
                )
            )

        return (
            enhanced_scene_packages,
            retrieval_by_scene,
            sorted(question_traces, key=lambda item: (item["scene_id"], item["query"])),
        )

    async def _draft_scene_intent_single(
        self,
        *,
        scene_id: str,
        delta_input: dict[str, Any],
        language: str,
        instructions: str,
        conversation_id: str | None,
        debug_collector: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("V1 novelist intent drafting has been removed")
        user_payload = json.dumps(
            {
                "scene_id": scene_id,
                "delta_from_step_3": delta_input,
            },
            ensure_ascii=True,
        )
        raw, _, _ = await self._call_llm(
            model=self._model_step_2_4,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.2,
            conversation_id=conversation_id,
            use_conversation_memory=True,
            debug_collector=debug_collector,
        )
        parsed = await self._parse_json_object_checked(raw) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "what_happens": self._normalize_text_list(parsed.get("what_happens", []), max_items=8),
            "emotional_progression": self._normalize_text_list(
                parsed.get("emotional_progression", []), max_items=8
            ),
            "speaking_goals": self._normalize_text_list(parsed.get("speaking_goals", []), max_items=8),
            "implied_history": self._normalize_text_list(parsed.get("implied_history", []), max_items=8),
            "forbidden_contradictions": self._normalize_text_list(
                parsed.get("forbidden_contradictions", []), max_items=8
            ),
        }

    async def _generate_scene_paragraph_single(
        self,
        *,
        scene_id: str,
        delta_input: dict[str, Any],
        language: str,
        instructions: str,
        conversation_id: str | None,
        debug_collector: list[dict[str, Any]] | None = None,
    ) -> str:
        raise RuntimeError("V1 novelist prose generation has been removed")
        user_payload = json.dumps(
            {
                "scene_id": scene_id,
                "delta_from_step_4": delta_input,
            },
            ensure_ascii=True,
        )
        raw, _, _ = await self._call_llm(
            model=self._model_step_5_7,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.25,
            conversation_id=conversation_id,
            use_conversation_memory=True,
            debug_collector=debug_collector,
        )
        html = self._ensure_readable_html(raw)
        return html

    async def _critic_scene_set(
        self,
        *,
        scene_packages: list[dict[str, Any]],
        prose_by_scene: list[dict[str, Any]],
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        system_prompt = self._compose_system_prompt(
            NOVELIST_STEP_6_CRITIC_PROMPT,
            language=language,
            instructions=instructions,
        )
        merged_text = self._merge_scene_html(prose_by_scene)
        merged_text = self._clip_text(merged_text, max_chars=self._critic_input_max_chars)
        user_payload = merged_text
        raw, latency_ms, _ = await self._call_llm(
            model=self._model_step_6,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.1,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        parsed = await self._parse_json_object_checked(raw) or {}
        by_scene = parsed.get("by_scene") if isinstance(parsed, dict) else None
        if not isinstance(by_scene, dict):
            by_scene = {}
        normalized_by_scene: dict[str, dict[str, list[str]]] = {}
        for scene in scene_packages:
            scene_name = str(scene.get("name") or scene.get("scene_id") or "").strip()
            row = by_scene.get(scene_name) if isinstance(by_scene.get(scene_name), dict) else {}
            normalized_by_scene[scene_name] = {
                "continuity_issues": self._normalize_text_list(row.get("continuity_issues", []), max_items=8),
                "duplication": self._normalize_text_list(row.get("duplication", []), max_items=8),
                "missing_transitions": self._normalize_text_list(row.get("missing_transitions", []), max_items=8),
                "voice_drift": self._normalize_text_list(row.get("voice_drift", []), max_items=8),
                "pacing": self._normalize_text_list(row.get("pacing", []), max_items=8),
                "graph_contradictions": self._normalize_text_list(row.get("graph_contradictions", []), max_items=8),
                "exposition_problems": self._normalize_text_list(row.get("exposition_problems", []), max_items=8),
            }

        return {
            "global_notes": self._normalize_text_list(parsed.get("global_notes", []), max_items=20)
            if isinstance(parsed, dict)
            else [],
            "by_scene": normalized_by_scene,
            "latency_ms": latency_ms,
        }

    async def _revise_scene_set(
        self,
        *,
        scene_packages: list[dict[str, Any]],
        prose_by_scene: list[dict[str, Any]],
        critic: dict[str, Any],
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        raise RuntimeError("V1 novelist revision has been removed")
        user_payload = (
            "Use the draft text and critic notes already present in this conversation memory "
            "from the immediately previous turn. Rewrite the complete chapter accordingly.\n\n"
            "Return revised prose HTML only."
        )
        raw, latency_ms, _ = await self._call_llm(
            model=self._model_step_5_7,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.2,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        revised_html = self._ensure_readable_html(raw)
        normalized_scenes = [
            {
                "scene_id": "scene-rev-001",
                "name": "Final Revised Draft",
                "prose_html": revised_html,
                "merged_from": [str(item.get("scene_id")) for item in prose_by_scene if item.get("scene_id")],
                "split_from": None,
                "notes": [],
            }
        ]
        lineage = {
            str(item.get("scene_id")): {
                "source_scene_ids": [item.get("scene_id")],
                "action": "merged",
            }
            for item in prose_by_scene
            if item.get("scene_id")
        }

        return {
            "scenes": normalized_scenes,
            "lineage": lineage,
            "global_revision_notes": [],
            "final_text_html": revised_html,
            "latency_ms": latency_ms,
        }

    async def _execute_v2(
        self,
        *,
        agent: Agent,
        step_1_scenes: list[dict[str, Any]],
        language: str,
        instructions: str,
        conversation_id: str | None,
        stage_callback: StageCallback | None,
        artifacts: dict[str, Any],
        started_total: float,
    ) -> dict[str, Any]:
        # Reset and collect per-call usage traces for this run (all steps).
        self._debug_response_calls = []
        # Reuse Architect scene outputs directly (including Architect merge decisions).
        merged_chunks = [dict(scene) for scene in step_1_scenes if isinstance(scene, dict)]
        if stage_callback:
            await stage_callback(
                NovelistStage.SCENE_PACKAGE,
                {
                    "artifacts": artifacts,
                    "scene_count": len(merged_chunks),
                },
            )
        for chunk in merged_chunks:
            self._debug_step_label = "step_2"
            planned = await self._plan_retrieval_questions_for_chunk(
                chunk=chunk,
                language=language,
                instructions=instructions,
                conversation_id=conversation_id,
            )
            if len(planned) >= 2:
                chunk["open_questions_for_retrieval"] = planned[:3]
                chunk["prior_knowledge_needed"] = [
                    {"question": q, "answer": ""}
                    for q in planned[:3]
                ]
        self._debug_step_label = None
        artifacts["stages"]["chunk_merge"] = {
            "count": len(merged_chunks),
            "chunks": merged_chunks,
            "merge_mode": "architect_scene_passthrough",
        }

        self._debug_step_label = "step_3"
        total_retrieval_questions = sum(
            len(self._normalize_prior_knowledge_pairs(chunk.get("prior_knowledge_needed"), scene_name=str(chunk.get("name") or "")))
            for chunk in merged_chunks
            if isinstance(chunk, dict)
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.RETRIEVAL,
                {
                    "artifacts": artifacts,
                    "scene_count": len(merged_chunks),
                    "total_questions": total_retrieval_questions,
                    "retrieved_questions": 0,
                },
            )
        enhanced_chunks, retrieval_by_scene, _ = await self._collect_scene_retrieval(
            agent=agent,
            scene_packages=merged_chunks,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        self._debug_step_label = None
        retrieved_questions = 0
        for retrieval in retrieval_by_scene.values():
            if not isinstance(retrieval, dict):
                continue
            qa = retrieval.get("questions_answers")
            if isinstance(qa, list):
                retrieved_questions += sum(
                    1
                    for row in qa
                    if isinstance(row, dict) and str(row.get("answer") or "").strip()
                )
        if stage_callback:
            await stage_callback(
                NovelistStage.RETRIEVAL,
                {
                    "artifacts": artifacts,
                    "scene_count": len(merged_chunks),
                    "total_questions": total_retrieval_questions,
                    "retrieved_questions": retrieved_questions,
                },
            )
        artifacts["stages"]["retrieval"] = retrieval_by_scene

        prose_by_scene: list[dict[str, Any]] = []
        if stage_callback:
            await stage_callback(
                NovelistStage.PROSE_GENERATION,
                {
                    "artifacts": artifacts,
                    "scene_count": len(enhanced_chunks),
                    "completed_scenes": 0,
                },
            )
        for chunk in enhanced_chunks:
            chunk_id = str(chunk.get("scene_id") or "")
            scene_conversation_id = self._build_scene_4_5_conversation_id(
                base_conversation_id=conversation_id,
                scene_id=chunk_id,
            )
            self._debug_step_label = "step_4"
            context = await self._build_chunk_context_v2(
                chunk=chunk,
                retrieval=retrieval_by_scene.get(chunk_id, {}),
                language=language,
                instructions=instructions,
                conversation_id=scene_conversation_id,
            )
            self._debug_step_label = "step_5"
            prose_html = await self._generate_merged_chunk_draft_v2(
                chunk={**chunk, "v2_context": context},
                language=language,
                instructions=instructions,
                conversation_id=scene_conversation_id,
            )
            self._debug_step_label = None
            prose_by_scene.append(
                {
                    "scene_id": chunk_id,
                    "name": str(chunk.get("name") or chunk_id),
                    "scene_summary": str(chunk.get("scene_summary") or ""),
                    "prose_html": prose_html,
                }
            )
            if stage_callback:
                await stage_callback(
                    NovelistStage.PROSE_GENERATION,
                    {
                        "artifacts": artifacts,
                        "scene_count": len(enhanced_chunks),
                        "completed_scenes": len(prose_by_scene),
                    },
                )

        artifacts["stages"]["prose_generation"] = {"count": len(prose_by_scene), "scene_paragraphs": prose_by_scene}
        critic_conversation_id = self._build_step_6_7_conversation_id(conversation_id)
        self._debug_step_label = "step_6"
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {
                    "artifacts": artifacts,
                    "scene_count": len(prose_by_scene),
                },
            )
        critic = await self._critic_scene_set(
            scene_packages=enhanced_chunks,
            prose_by_scene=prose_by_scene,
            language=language,
            instructions=instructions,
            conversation_id=critic_conversation_id,
        )
        self._debug_step_label = "step_7"
        if stage_callback:
            await stage_callback(
                NovelistStage.REVISION,
                {
                    "artifacts": artifacts,
                    "scene_count": len(prose_by_scene),
                },
            )
        revision = await self._revise_scene_set_v2(
            prose_by_scene=prose_by_scene,
            critic=critic,
            language=language,
            instructions=instructions,
            conversation_id=critic_conversation_id,
        )
        self._debug_step_label = None
        final_html = str(revision.get("final_text_html") or "").strip()
        artifacts["stages"]["merging"] = {"scene_count": len(prose_by_scene), "final_text": final_html}
        artifacts["timings_ms"]["total"] = round((time.monotonic() - started_total) * 1000, 2)
        artifacts["v2_metrics"] = {
            "merged_chunk_count": len(merged_chunks),
            "elder_calls": len(merged_chunks),
            "draft_calls": len(prose_by_scene),
        }
        artifacts["llm_call_summary"] = {
            "mode": "v2",
            "estimated_v1_calls": (4 * len(step_1_scenes)) + 2,
            "estimated_v2_calls": (2 * len(merged_chunks)) + 2,
        }
        artifacts["llm_usage_by_step_novelist"] = self._build_llm_usage_by_step_from_debug_calls(
            self._debug_response_calls
        )
        if stage_callback:
            await stage_callback(NovelistStage.MERGING, {"artifacts": artifacts, "draft_text": final_html})
        return {
            "scene_packages": enhanced_chunks,
            "critic_remarks": critic,
            "final_text_html": final_html,
            "artifacts": artifacts,
            "scene_results": prose_by_scene,
            "timing_summary": {
                "total_ms": artifacts["timings_ms"].get("total", 0.0),
                "by_stage_ms": artifacts["timings_ms"],
                "scene_count": len(prose_by_scene),
            },
            "draft_text": final_html,
            "conversation_id": conversation_id,
        }

    @staticmethod
    def _build_llm_usage_by_step_from_debug_calls(
        calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_step: dict[str, dict[str, Any]] = {}
        totals = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        for call in calls:
            if not isinstance(call, dict):
                continue
            step = str(call.get("step") or "").strip() or "unknown"
            usage = call.get("token_usage")
            if not isinstance(usage, dict):
                usage = {}
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or 0)

            bucket = by_step.setdefault(
                step,
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += prompt
            bucket["completion_tokens"] += completion
            bucket["total_tokens"] += total

            totals["calls"] += 1
            totals["prompt_tokens"] += prompt
            totals["completion_tokens"] += completion
            totals["total_tokens"] += total

        return {"totals": totals, "by_step": by_step}

    def _build_merged_chunks_v2(self, scenes: list[dict[str, Any]], *, max_chunks: int = 5) -> list[dict[str, Any]]:
        if not scenes:
            return []
        chunk_count = min(max_chunks, len(scenes))
        chunk_size = (len(scenes) + chunk_count - 1) // chunk_count
        merged: list[dict[str, Any]] = []
        for idx in range(0, len(scenes), chunk_size):
            bundle = scenes[idx : idx + chunk_size]
            chunk_entities = self._dedupe_and_limit(
                [
                    str(item.get("entity") or "").strip()
                    for scene in bundle
                    for item in (scene.get("related_entities") or [])
                    if isinstance(item, dict) and str(item.get("entity") or "").strip()
                ],
                limit=6,
            )
            fallback_entities = ", ".join(chunk_entities[:3]) if chunk_entities else "the core cast"
            first_scene_name = str(bundle[0].get("name") or f"Merged Chunk {len(merged)+1}").strip()
            chunk_summary = " ".join(
                str(s.get("scene_summary") or "").strip() for s in bundle
            ).strip()
            top_entities = chunk_entities[:3]
            if top_entities:
                open_questions_for_retrieval = [
                    f"What happened earlier between {top_entities[0]} and {top_entities[1] if len(top_entities) > 1 else top_entities[0]} that should shape decisions in '{first_scene_name}'?",
                    f"What unresolved goal or pressure currently drives {top_entities[0]} in '{first_scene_name}'?",
                    f"What constraint from prior events must remain true in '{first_scene_name}' to avoid continuity breaks?",
                ]
            else:
                open_questions_for_retrieval = [
                    f"What earlier event most directly causes the conflict in '{first_scene_name}'?",
                    f"What unresolved pressure should be visible in character choices in '{first_scene_name}'?",
                    f"What continuity constraint must remain true in '{first_scene_name}'?",
                ]
            prior_knowledge_needed = [
                {"question": q, "answer": ""} for q in open_questions_for_retrieval
            ]
            merged.append(
                {
                    "scene_id": f"chunk-{len(merged)+1:03d}",
                    "name": f"Merged Chunk {len(merged)+1}",
                    "scene_summary": chunk_summary,
                    "source_rawtext": "\n\n".join(
                        str(s.get("source_rawtext") or s.get("raw_scene_text") or "").strip()
                        for s in bundle
                        if str(s.get("source_rawtext") or s.get("raw_scene_text") or "").strip()
                    ),
                    "merged_from_scene_ids": [str(s.get("scene_id") or "") for s in bundle if str(s.get("scene_id") or "").strip()],
                    "related_entities": chunk_entities,
                    "open_questions_for_retrieval": open_questions_for_retrieval,
                    "prior_knowledge_needed": prior_knowledge_needed,
                }
            )
        return merged

    async def _plan_retrieval_questions_for_chunk(
        self,
        *,
        chunk: dict[str, Any],
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> list[str]:
        system_prompt = self._compose_system_prompt(
            NOVELIST_STEP_2_RETRIEVAL_QUESTION_PLANNER_PROMPT,
            language=language,
            instructions=instructions,
        )
        user_payload = json.dumps(
            {
                "scene_id": str(chunk.get("scene_id") or ""),
                "scene_name": str(chunk.get("name") or ""),
                "scene_summary": str(chunk.get("scene_summary") or ""),
            },
            ensure_ascii=True,
        )
        try:
            raw, _, _ = await self._call_llm(
                model=self._model_step_2_4,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.1,
                conversation_id=conversation_id,
                use_conversation_memory=True,
            )
            parsed = await self._parse_json_object_checked(
                raw,
                schema_hint='{"questions":["...","...","..."]}',
                usage_tag="agents.json_repair",
            )
            values = parsed.get("questions") if isinstance(parsed, dict) else []
            if not isinstance(values, list):
                return []
            cleaned = [
                str(v).strip()
                for v in values
                if str(v or "").strip()
            ]
            # hard cap and dedupe
            return self._dedupe_and_limit(cleaned, limit=3)
        except Exception:
            return []

    @staticmethod
    def _compact_chunk_payload_for_llm(chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "scene_id": str(chunk.get("scene_id") or ""),
            "name": str(chunk.get("name") or ""),
            "scene_summary": str(chunk.get("scene_summary") or ""),
            "source_rawtext": str(
                chunk.get("source_rawtext") or chunk.get("raw_scene_text") or ""
            ),
            "merged_from_scene_ids": chunk.get("merged_from_scene_ids", []),
            "related_entities": chunk.get("related_entities", []),
            "open_questions_for_retrieval": chunk.get("open_questions_for_retrieval", []),
            "prior_knowledge_needed": chunk.get("prior_knowledge_needed", []),
            "v2_context": chunk.get("v2_context", {}),
        }

    async def _build_chunk_context_v2(self, *, chunk: dict[str, Any], retrieval: dict[str, Any], language: str, instructions: str, conversation_id: str | None) -> dict[str, str]:
        system_prompt = self._compose_system_prompt(NOVELIST_STEP_4_CONTEXT_BUILD_PROMPT, language=language, instructions=instructions)
        prior_knowledge: dict[str, str] = {}
        for row in (retrieval.get("questions_answers", []) if isinstance(retrieval, dict) else []):
            if not isinstance(row, dict):
                continue
            question = str(row.get("question") or "").strip()
            answer = str(row.get("answer") or "").strip()
            if question and answer:
                prior_knowledge[question] = answer
        user_payload = json.dumps(
            {
                "scene_name": str(chunk.get("name") or chunk.get("scene_id") or ""),
                "scene_description": str(chunk.get("scene_summary") or ""),
                "prior_knowledge": prior_knowledge,
            },
            ensure_ascii=True,
        )
        raw, _, _ = await self._call_llm(
            model=self._model_step_2_4,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_payload}],
            temperature=0.1,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        parsed = await self._parse_json_object_checked(raw) or {}
        return {k: str(parsed.get(k) or "").strip() for k in self._empty_retrieval_buckets().keys()}

    async def _generate_merged_chunk_draft_v2(self, *, chunk: dict[str, Any], language: str, instructions: str, conversation_id: str | None) -> str:
        system_prompt = self._compose_system_prompt(NOVELIST_STEP_5_DRAFT_PROMPT, language=language, instructions=instructions)
        # Step 5 relies on shared conversation memory from step 4 for this chunk.
        # Send only minimal framing payload to avoid duplicate large context resend.
        user_payload = json.dumps(
            {
                "scene_id": str(chunk.get("scene_id") or ""),
                "scene_name": str(chunk.get("name") or ""),
                "scene_summary": str(chunk.get("scene_summary") or ""),
                "instruction": "Write the merged chunk prose using prior scene context from this conversation.",
            },
            ensure_ascii=True,
        )
        raw, _, _ = await self._call_llm(
            model=self._model_step_5_7,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        return self._ensure_readable_html(raw)

    async def _revise_scene_set_v2(self, *, prose_by_scene: list[dict[str, Any]], critic: dict[str, Any], language: str, instructions: str, conversation_id: str | None) -> dict[str, Any]:
        system_prompt = self._compose_system_prompt(NOVELIST_STEP_7_FINAL_REWRITE_PROMPT, language=language, instructions=instructions)
        user_payload = json.dumps(
            {
                "draft_html": self._merge_scene_html(prose_by_scene),
                "critic": critic,
            },
            ensure_ascii=True,
        )
        raw, latency_ms, _ = await self._call_llm(
            model=self._model_step_5_7,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_payload}],
            temperature=0.2,
            conversation_id=conversation_id,
            use_conversation_memory=True,
        )
        revised_html = self._ensure_readable_html(raw)
        return {"scenes": [{"scene_id": "scene-rev-v2-001", "name": "Final Revised Draft", "prose_html": revised_html}], "lineage": {}, "global_revision_notes": [], "final_text_html": revised_html, "latency_ms": latency_ms}

    @staticmethod
    def _empty_retrieval_buckets() -> dict[str, list[str]]:
        return {
            "prior_events": [],
            "relationship_summaries": [],
            "personality_reminders": [],
            "unresolved_tensions": [],
            "style_details": [],
            "contradiction_warnings": [],
        }

    def _filter_scene_retrieval(self, sources: list[dict[str, Any]]) -> dict[str, list[str]]:
        buckets = self._empty_retrieval_buckets()

        for source in sources[:10]:
            line = self._compact_retrieved_text(str(source.get("text") or ""), max_chars=220)
            if not line:
                continue
            lowered = line.lower()
            label = str(source.get("node_label") or "").lower()

            if any(token in lowered for token in ("contradict", "inconsistent", "does not match")):
                buckets["contradiction_warnings"].append(line)
            if any(token in lowered for token in ("tension", "conflict", "resent", "grudge")):
                buckets["unresolved_tensions"].append(line)
            if any(token in lowered for token in ("temper", "personality", "voice", "ideal", "belief")):
                buckets["personality_reminders"].append(line)
            if any(token in lowered for token in ("style", "speaks", "phrasing", "mannerism", "gesture")):
                buckets["style_details"].append(line)
            if any(token in lowered for token in ("relationship", "ally", "enemy", "bond", "rival")):
                buckets["relationship_summaries"].append(line)
            if label in {"scene", "milestone"} or any(
                token in lowered for token in ("before", "after", "earlier", "previously")
            ):
                buckets["prior_events"].append(line)

        # Fallback fill so retrieval still contributes deterministic short context.
        flattened = [
            self._compact_retrieved_text(str(source.get("text") or ""), max_chars=220)
            for source in sources[:10]
        ]
        flattened = [line for line in flattened if line]
        if flattened and not any(buckets.values()):
            buckets["prior_events"] = flattened[:4]

        for key, values in buckets.items():
            buckets[key] = self._dedupe_and_limit(values, limit=6)

        return buckets

    @staticmethod
    def _parse_architect_entities(payload: dict[str, Any]) -> list[str]:
        try:
            parsed = ChunkExtractionResponse.model_validate(payload)
        except Exception:
            return []
        entities = [item.name.strip() for item in parsed.entities if item.name and item.name.strip()]
        return NovelistOrchestrator._dedupe_and_limit(entities, limit=20)

    @staticmethod
    def _parse_milestones(payload: dict[str, Any]) -> list[str]:
        items = payload.get("milestones") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            items = []

        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("label") or "")
            description = str(item.get("description") or "").strip()
            boundary_type = str(item.get("boundary_type") or "none").strip().lower()
            if boundary_type in {"start"}:
                boundary_type = "begin"
            elif boundary_type in {"finish", "stop"}:
                boundary_type = "end"
            elif boundary_type not in {"begin", "end", "none"}:
                boundary_type = "none"
            normalized.append(
                {
                    "title": title,
                    "description": description,
                    "boundary_type": boundary_type,
                }
            )

        if not normalized:
            normalized = [
                {
                    "title": "Scene opening beat",
                    "description": "The scene begins.",
                    "boundary_type": "begin",
                },
                {
                    "title": "Scene closing beat",
                    "description": "The scene ends.",
                    "boundary_type": "end",
                },
            ]
        elif len(normalized) == 1:
            only = normalized[0]
            normalized = [
                {**only, "boundary_type": "begin"},
                {**only, "boundary_type": "end"},
            ]
        else:
            if not any(item["boundary_type"] == "begin" for item in normalized):
                normalized[0]["boundary_type"] = "begin"
            if not any(item["boundary_type"] == "end" for item in normalized):
                normalized[-1]["boundary_type"] = "end"

        values: list[str] = []
        for item in normalized:
            title = item["title"]
            description = item["description"]
            if title and description:
                values.append(f"{title}: {description}")
            elif title:
                values.append(title)
            elif description:
                values.append(description)
        return NovelistOrchestrator._dedupe_and_limit(values, limit=12)

    @staticmethod
    def _serialize_ontology_definitions(agent: Agent) -> str:
        lines: list[str] = []
        for ontology in getattr(agent, "ontologies", []) or []:
            ont_name = str(getattr(ontology, "name", "Ontology")).strip() or "Ontology"
            entities = getattr(ontology, "entities", None) or []
            if not entities:
                lines.append(f"- {ont_name}: no entity definitions")
                continue
            lines.append(f"- {ont_name}:")
            for entity in entities:
                entity_name = str(getattr(entity, "name", "Entity")).strip()
                entity_desc = str(getattr(entity, "description", "") or "").strip()
                if entity_desc:
                    lines.append(f"  - {entity_name}: {entity_desc}")
                else:
                    lines.append(f"  - {entity_name}")
        return "\n".join(lines) if lines else "- Character\n- Place\n- Faction\n- Item"

    def _build_step_1_scenes(
        self,
        scenes: list[dict[str, Any]],
        *,
        language_output_text: str,
        instructions: str,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            scene_id = str(scene.get("scene_id") or f"scene-{idx:03d}").strip()
            if not scene_id:
                scene_id = f"scene-{idx:03d}"
            name_raw = scene.get("name")
            name = str(name_raw) if name_raw is not None else scene_id
            if not str(name).strip():
                name = scene_id
            out.append(
                {
                    "scene_id": scene_id,
                    "name": name,
                    "scene_summary": str(scene.get("scene_summary") or "").strip(),
                    "source_rawtext": str(
                        scene.get("source_rawtext") or scene.get("raw_scene_text") or ""
                    ).strip(),
                    "milestones": self._normalize_title_list(scene.get("milestones", []), max_items=8),
                    "related_entities": self._normalize_related_entities(
                        scene.get("related_entities", []),
                    ),
                    "instructions": str(scene.get("instructions") or instructions or "").strip(),
                    "Language_output_text": str(
                        scene.get("Language_output_text") or language_output_text or ""
                    ).strip(),
                }
            )
        return out

    def _build_step_2_scene_packages(
        self,
        *,
        scene_packages: list[dict[str, Any]],
        step_1_scenes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_scene_id = {str(scene.get("scene_id")): scene for scene in step_1_scenes}
        out: list[dict[str, Any]] = []
        for idx, package in enumerate(scene_packages, start=1):
            if not isinstance(package, dict):
                continue
            scene_id = str(package.get("scene_id") or f"scene-{idx:03d}").strip()
            source = by_scene_id.get(scene_id, {})
            out.append(
                {
                    "scene_id": scene_id,
                    "source_rawtext": str(
                        package.get("source_rawtext")
                        or package.get("raw_scene_text")
                        or source.get("source_rawtext")
                        or source.get("raw_scene_text")
                        or ""
                    ).strip(),
                    "scene_summary": str(
                        package.get("scene_summary") or source.get("scene_summary") or ""
                    ).strip(),
                    "name": str(package.get("name") or source.get("name") or scene_id).strip(),
                    "Language_output_text": str(
                        package.get("Language_output_text")
                        or source.get("Language_output_text")
                        or ""
                    ).strip(),
                    "scene_goal": str(
                        package.get("scene_goal")
                        or package.get("scene_summary")
                        or source.get("scene_summary")
                        or ""
                    ).strip(),
                    "scene_tone": str(package.get("scene_tone") or "").strip(),
                    "prior_knowledge_needed": self._normalize_prior_knowledge_pairs(
                        package.get("prior_knowledge_needed"),
                        scene_name=str(package.get("name") or source.get("name") or scene_id),
                    ),
                    "milestones": self._normalize_text_list(
                        package.get("milestones") or source.get("milestones") or [],
                        max_items=8,
                    ),
                    "related_entities": self._normalize_related_entities(
                        package.get("related_entities") or source.get("related_entities") or [],
                    ),
                    "instructions": str(
                        package.get("instructions") or source.get("instructions") or ""
                    ).strip(),
                }
            )
        out.sort(key=lambda item: str(item.get("scene_id", "")))
        return out

    @staticmethod
    def _normalize_prior_knowledge_pairs(value: Any, *, scene_name: str) -> list[dict[str, str]]:
        pairs: list[dict[str, str]] = []
        seen_questions: set[str] = set()
        if isinstance(value, dict):
            for question, answer in value.items():
                q = re.sub(r"\s+", " ", str(question or "")).strip()
                a = re.sub(r"\s+", " ", str(answer or "")).strip()
                if not q or q.lower() in seen_questions:
                    continue
                seen_questions.add(q.lower())
                pairs.append({"question": q[:220], "answer": a[:260]})
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    q = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
                    a = re.sub(r"\s+", " ", str(item.get("answer") or "")).strip()
                else:
                    q = re.sub(r"\s+", " ", str(item or "")).strip()
                    a = ""
                if not q or q.lower() in seen_questions:
                    continue
                seen_questions.add(q.lower())
                pairs.append({"question": q[:220], "answer": a[:260]})
                if len(pairs) >= 5:
                    break
        if pairs:
            return pairs[:5]
        default_question = f"What prior event most affects {scene_name or 'this scene'}?"
        return [{"question": default_question[:220], "answer": ""}]

    def _questions_from_prior_knowledge_pairs(self, value: Any, *, fallback: Any) -> list[str]:
        pairs = self._normalize_prior_knowledge_pairs(value, scene_name="")
        questions = self._normalize_text_list(
            [item.get("question", "") for item in pairs if isinstance(item, dict)],
            max_items=5,
        )
        if questions:
            return questions
        return self._normalize_text_list(fallback if isinstance(fallback, list) else [], max_items=5)

    def _apply_retrieval_to_scene(self, *, scene: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
        merged = dict(scene)
        buckets = retrieval.get("buckets") if isinstance(retrieval, dict) else {}
        if not isinstance(buckets, dict):
            buckets = {}
        summary_fields = (
            "prior_events",
            "relationship_summaries",
            "personality_reminders",
            "unresolved_tensions",
            "style_details",
            "contradiction_warnings",
        )
        for key in summary_fields:
            values = buckets.get(key, [])
            if isinstance(values, list):
                merged[key] = str(values[0]).strip() if values else ""
            else:
                merged[key] = str(values or "").strip()
        merged["questions_answers"] = retrieval.get("questions_answers", []) if isinstance(retrieval, dict) else []
        merged["queries"] = retrieval.get("queries", []) if isinstance(retrieval, dict) else []
        merged["Language_output_text"] = str(
            merged.get("Language_output_text") or ""
        ).strip()
        return merged

    def _normalize_related_entities(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            entity_name = ""
            if isinstance(value, dict):
                entity_name = str(
                    value.get("entity")
                    or value.get("name")
                    or value.get("alias")
                    or value.get("value")
                    or ""
                ).strip()
            else:
                entity_name = str(value or "").strip()
            if not entity_name:
                continue
            key = entity_name.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(entity_name)
            if len(normalized) >= 12:
                break
        return normalized

    @staticmethod
    def _build_scene_results(
        *,
        scene_packages: list[dict[str, Any]],
        retrieval_by_scene: dict[str, dict[str, Any]],
        intents_by_scene: dict[str, dict[str, Any]],
        prose_by_scene: list[dict[str, Any]],
        critic: dict[str, Any],
        revision: dict[str, Any],
    ) -> list[dict[str, Any]]:
        prose_map = {str(item.get("scene_id")): item for item in prose_by_scene}
        critic_by_scene = critic.get("by_scene", {}) if isinstance(critic, dict) else {}
        lineage = revision.get("lineage", {}) if isinstance(revision, dict) else {}

        out: list[dict[str, Any]] = []
        for order, scene in enumerate(scene_packages, start=1):
            scene_id = str(scene.get("scene_id"))
            scene_name = str(scene.get("name") or scene_id)
            prose = prose_map.get(scene_id, {})
            critic_row = critic_by_scene.get(scene_name, {}) if isinstance(critic_by_scene, dict) else {}
            issue_count = sum(
                len(values)
                for values in critic_row.values()
                if isinstance(values, list)
            )
            lineage_row = lineage.get(scene_id, {}) if isinstance(lineage, dict) else {}
            out.append(
                {
                    "scene_id": scene_id,
                    "order": order,
                    "scene_summary": scene.get("scene_summary", ""),
                    "scene_goal": scene.get("scene_goal", ""),
                    "milestones": scene.get("milestones", []),
                    "related_entities": scene.get("related_entities", []),
                    "retrieval": retrieval_by_scene.get(scene_id, {}).get("buckets", {}),
                    "intent": intents_by_scene.get(scene_id, {}),
                    "prose_html": prose.get("prose_html", ""),
                    "critic": critic_row,
                    "critic_issue_count": issue_count,
                    "revision_action": str(lineage_row.get("action") or "kept"),
                    "lineage": lineage_row,
                }
            )
        return out

    @staticmethod
    def _build_step_outputs(
        *,
        scaffolding: dict[str, Any],
        scene_packages: list[dict[str, Any]],
        retrieval_by_scene: dict[str, dict[str, Any]],
        intents_by_scene: dict[str, dict[str, Any]],
        prose_by_scene: list[dict[str, Any]],
        critic: dict[str, Any],
        revision: dict[str, Any],
        final_html: str,
    ) -> dict[str, Any]:
        ordered_scene_ids = [str(item.get("scene_id")) for item in scene_packages]
        prose_map = {str(item.get("scene_id")): item for item in prose_by_scene}

        step_1_scenes: list[dict[str, Any]] = []
        for scene in scaffolding.get("scenes", []) if isinstance(scaffolding, dict) else []:
            if not isinstance(scene, dict):
                continue
            step_1_scenes.append(
                {
                    "scene_id": scene.get("scene_id"),
                    "name": scene.get("name"),
                    "scene_summary": scene.get("scene_summary", ""),
                    "source_rawtext": scene.get("source_rawtext", ""),
                    "milestones": scene.get("milestones", []),
                    "related_entities": scene.get("related_entities", []),
                    "instructions": scene.get("instructions", ""),
                    "Language_output_text": scene.get("Language_output_text", ""),
                }
            )

        step_3_context: list[dict[str, Any]] = []
        for scene_id in ordered_scene_ids:
            retrieval = retrieval_by_scene.get(scene_id, {})
            step_3_context.append(
                {
                    "scene_id": scene_id,
                    "queries": retrieval.get("queries", []),
                    "questions_answers": retrieval.get("questions_answers", []),
                    "narrative_context": retrieval.get("buckets", {}),
                    "instructions": retrieval.get("instructions", ""),
                    "enhanced_scene_payload": next(
                        (
                            scene
                            for scene in scene_packages
                            if str(scene.get("scene_id")) == scene_id
                        ),
                        {},
                    ),
                }
            )

        step_4_intents: list[dict[str, Any]] = []
        for scene_id in ordered_scene_ids:
            step_4_intents.append(intents_by_scene.get(scene_id, {"scene_id": scene_id}))

        step_5_prose: list[dict[str, Any]] = []
        for scene_id in ordered_scene_ids:
            prose = prose_map.get(scene_id, {})
            step_5_prose.append(
                {
                    "scene_id": scene_id,
                    "name": prose.get("name") or scene_id,
                    "scene_summary": prose.get("scene_summary", ""),
                    "prose_html": prose.get("prose_html", ""),
                }
            )

        return {
            "step_1": {
                "label": "scene_scaffolding",
                "scenes": step_1_scenes,
            },
            "step_2": {
                "label": "scene_writing_packages",
                "scene_packages": scene_packages,
            },
            "step_3": {
                "label": "scene_narrative_context",
                "narrative_context_by_scene": step_3_context,
            },
            "step_4": {
                "label": "scene_intended_draft_output",
                "scene_intents": step_4_intents,
            },
            "step_5": {
                "label": "scene_prose_output",
                "scene_prose": step_5_prose,
            },
            "step_6": {
                "label": "critic_response",
                "critic": critic,
            },
            "step_7": {
                "label": "full_rewritten_text",
                "final_rewritten_text": final_html,
                "revised_scenes": revision.get("scenes", []) if isinstance(revision, dict) else [],
                "lineage": revision.get("lineage", {}) if isinstance(revision, dict) else {},
            },
        }

    @staticmethod
    def _merge_scene_html(prose_by_scene: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for idx, item in enumerate(prose_by_scene, start=1):
            title = str(item.get("name") or item.get("scene_id") or f"Scene {idx}").strip()
            html = str(item.get("prose_html") or "").strip()
            blocks.append(f"<h1>{title}</h1>\n{html}")
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _merge_revised_scene_html(revised_scenes: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for idx, item in enumerate(revised_scenes, start=1):
            title = str(item.get("name") or item.get("scene_id") or f"Scene {idx}").strip()
            html = str(item.get("prose_html") or "").strip()
            blocks.append(f"<h1>{title}</h1>\n{html}")
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _compose_system_prompt(base_prompt: str, *, language: str, instructions: str) -> str:
        language_rule = (
            f"- MANDATORY OUTPUT LANGUAGE: {language}."
            if language
            else "- MANDATORY OUTPUT LANGUAGE: Match source language unless instructed otherwise."
        )
        instructions_rule = (
            f"- MANDATORY INSTRUCTIONS TO FOLLOW: {instructions}"
            if instructions
            else "- MANDATORY INSTRUCTIONS TO FOLLOW: None."
        )
        return (
            f"{base_prompt}\n\nGlobal mandatory constraints:\n"
            f"{language_rule}\n"
            f"{instructions_rule}\n"
            "- Output MUST follow the requested schema exactly when a JSON schema is requested."
        )

    @staticmethod
    def _normalize_text_list(values: Any, *, max_items: int, max_chars: int = 220) -> list[str]:
        if not isinstance(values, list):
            return []
        items: list[str] = []
        for raw_value in values:
            compact = re.sub(r"\s+", " ", str(raw_value)).strip()
            if not compact:
                continue
            candidate = compact[:max_chars]
            if candidate in items:
                continue
            items.append(candidate)
            if len(items) >= max_items:
                break
        return items

    @staticmethod
    def _dedupe_and_limit(values: list[str], *, limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            compact = re.sub(r"\s+", " ", str(value or "")).strip()
            if not compact:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(compact)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _normalize_title_list(values: Any, *, max_items: int, max_chars: int = 220) -> list[str]:
        if not isinstance(values, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item if item is not None else "")
            if not text.strip():
                continue
            key = text.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text[:max_chars] if max_chars > 0 else text)
            if len(out) >= max_items:
                break
        return out

    async def _parse_json_object_checked(
        self,
        raw: str,
        *,
        schema_hint: str | None = None,
        usage_tag: str = "agents.json_repair",
    ) -> Any:
        try:
            return json.loads(raw)
        except Exception:
            try:
                return await validate_or_repair_json(
                    llm_client=self.llm_client,
                    model=self.repair_json_model or self._model_step_2_4,
                    raw_text=raw,
                    schema_hint=schema_hint,
                    usage_tag=usage_tag,
                )
            except Exception:
                return None

    @staticmethod
    def _scene_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        keys = sorted(set(before.keys()) | set(after.keys()))
        delta: dict[str, Any] = {}
        for key in keys:
            before_value = before.get(key)
            after_value = after.get(key)
            if before_value == after_value:
                continue
            delta[key] = {
                "before": before_value,
                "after": after_value,
            }
        return delta

    @staticmethod
    def _scene_delta_allowlist(
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        allowed_fields: tuple[str, ...],
    ) -> dict[str, Any]:
        delta: dict[str, Any] = {}
        for key in allowed_fields:
            before_value = before.get(key)
            after_value = after.get(key)
            if before_value == after_value:
                continue
            delta[key] = {"before": before_value, "after": after_value}
        return delta

    @staticmethod
    def _lean_scene_payload(scene: dict[str, Any]) -> dict[str, Any]:
        return {
            "scene_id": str(scene.get("scene_id") or ""),
            "name": str(scene.get("name") or ""),
            "scene_summary": str(scene.get("scene_summary") or ""),
            "source_rawtext": str(scene.get("source_rawtext") or scene.get("raw_scene_text") or ""),
            "milestones": scene.get("milestones", []),
            "related_entities": scene.get("related_entities", []),
            "instructions": str(scene.get("instructions") or ""),
            "Language_output_text": str(scene.get("Language_output_text") or ""),
            "prior_knowledge_needed": scene.get("prior_knowledge_needed", []),
            "scene_tone": str(scene.get("scene_tone") or ""),
            "scene_goal": str(scene.get("scene_goal") or ""),
        }

    @staticmethod
    def _token_summary_from_scene_traces(scene_traces: list[dict[str, Any]]) -> dict[str, Any]:
        prompt_total = 0
        completion_total = 0
        total_total = 0
        call_count = 0
        per_scene: dict[str, dict[str, int]] = {}

        for scene_trace in scene_traces:
            scene_id = str(scene_trace.get("scene_id") or "")
            llm_calls = scene_trace.get("llm_calls", [])
            if not isinstance(llm_calls, list):
                continue
            scene_prompt = 0
            scene_completion = 0
            scene_total = 0
            for call in llm_calls:
                if not isinstance(call, dict):
                    continue
                usage = call.get("token_usage")
                if not isinstance(usage, dict):
                    continue
                call_count += 1
                prompt = usage.get("prompt_tokens")
                completion = usage.get("completion_tokens")
                total = usage.get("total_tokens")
                if isinstance(prompt, int):
                    prompt_total += prompt
                    scene_prompt += prompt
                if isinstance(completion, int):
                    completion_total += completion
                    scene_completion += completion
                if isinstance(total, int):
                    total_total += total
                    scene_total += total
            if scene_id:
                per_scene[scene_id] = {
                    "prompt_tokens": scene_prompt,
                    "completion_tokens": scene_completion,
                    "total_tokens": scene_total,
                }
        return {
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": total_total,
            "call_count": call_count,
            "per_scene": per_scene,
        }

    @staticmethod
    def _token_summary_from_calls(calls: list[dict[str, Any]]) -> dict[str, int]:
        prompt_total = 0
        completion_total = 0
        total_total = 0
        call_count = 0
        for call in calls:
            if not isinstance(call, dict):
                continue
            usage = call.get("token_usage")
            if not isinstance(usage, dict):
                continue
            call_count += 1
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            if isinstance(prompt, int):
                prompt_total += prompt
            if isinstance(completion, int):
                completion_total += completion
            if isinstance(total, int):
                total_total += total
        return {
            "call_count": call_count,
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": total_total,
        }

    @staticmethod
    def _models_from_calls(calls: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in calls:
            if not isinstance(call, dict):
                continue
            model = str(call.get("model") or "").strip()
            if not model:
                continue
            counts[model] = counts.get(model, 0) + 1
        return counts

    def _build_llm_call_summary(
        self,
        *,
        step_2_traces: list[dict[str, Any]],
        step_3_traces: list[dict[str, Any]],
        step_4_traces: list[dict[str, Any]],
        step_5_traces: list[dict[str, Any]],
        step_6_calls: list[dict[str, Any]],
        step_7_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        steps: dict[str, dict[str, Any]] = {}
        totals = {
            "call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        total_by_model: dict[str, int] = {}

        def add_step(step_id: str, calls: list[dict[str, Any]]) -> None:
            token_summary = self._token_summary_from_calls(calls)
            by_model = self._models_from_calls(calls)
            steps[step_id] = {
                "call_count": token_summary["call_count"],
                "prompt_tokens": token_summary["prompt_tokens"],
                "completion_tokens": token_summary["completion_tokens"],
                "total_tokens": token_summary["total_tokens"],
                "by_model": by_model,
            }
            totals["call_count"] += token_summary["call_count"]
            totals["prompt_tokens"] += token_summary["prompt_tokens"]
            totals["completion_tokens"] += token_summary["completion_tokens"]
            totals["total_tokens"] += token_summary["total_tokens"]
            for model_name, count in by_model.items():
                total_by_model[model_name] = total_by_model.get(model_name, 0) + count

        add_step("step_2", [row for row in step_2_traces for row in (row.get("llm_calls") or []) if isinstance(row, dict)])
        add_step("step_3", [row for row in step_3_traces for row in (row.get("llm_calls") or []) if isinstance(row, dict)])
        add_step("step_4", [row for row in step_4_traces for row in (row.get("llm_calls") or []) if isinstance(row, dict)])
        add_step("step_5", [row for row in step_5_traces for row in (row.get("llm_calls") or []) if isinstance(row, dict)])
        add_step("step_6", step_6_calls)
        add_step("step_7", step_7_calls)

        return {
            "totals": totals,
            "by_model": total_by_model,
            "by_step": steps,
        }

    async def _call_llm(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        conversation_id: str | None,
        use_conversation_memory: bool = False,
        debug_collector: list[dict[str, Any]] | None = None,
    ) -> tuple[str, float, dict[str, Any]]:
        started = time.monotonic()
        response_payload: dict[str, Any]
        try:
            response_payload = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                conversation_id=conversation_id,
                use_conversation_memory=use_conversation_memory,
                return_metadata=True,
            )
        except TypeError:
            raw_text = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                conversation_id=conversation_id,
            )
            response_payload = {
                "text": str(raw_text or ""),
                "usage": {
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                },
                "response_metadata": {},
            }
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        raw = str(response_payload.get("text") or "")
        token_usage = response_payload.get("usage")
        if not isinstance(token_usage, dict):
            token_usage = {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        debug_call = {
            "step": self._debug_step_label,
            "model": model,
            "temperature": temperature,
            "conversation_id": conversation_id,
            "latency_ms": elapsed_ms,
            "messages_pretty": self._messages_pretty(messages),
            "raw_response_lines": raw.splitlines(),
            "token_usage": token_usage,
            "response_metadata": response_payload.get("response_metadata", {}),
        }
        if debug_collector is not None:
            debug_collector.append(debug_call)
        if self._debug_step_label:
            self._debug_prompt_calls.append(
                {
                    "step": self._debug_step_label,
                    "model": model,
                    "temperature": temperature,
                    "conversation_id": conversation_id,
                    "messages_pretty": self._messages_pretty(messages),
                }
            )
            self._debug_response_calls.append(
                {
                    "step": self._debug_step_label,
                    "model": model,
                    "temperature": temperature,
                    "conversation_id": conversation_id,
                    "latency_ms": elapsed_ms,
                    "messages_pretty": self._messages_pretty(messages),
                    "raw_response_lines": raw.splitlines(),
                    "token_usage": token_usage,
                }
            )
        return raw, elapsed_ms, token_usage

    @staticmethod
    def _messages_pretty(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "").strip()
            content = str(message.get("content") or "")
            out.append(
                {
                    "role": role,
                    "content_lines": content.splitlines(),
                }
            )
        return out

    @staticmethod
    def _compact_retrieved_text(raw_text: str, max_chars: int = 200) -> str:
        compact = re.sub(r"<[^>]+>", " ", raw_text or "")
        compact = re.sub(r"\s+", " ", compact).strip()
        if not compact:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", compact) if s.strip()]
        seed = sentences[0] if sentences else compact
        return seed[:max_chars]

    @staticmethod
    def _clip_text(text: str, *, max_chars: int) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(compact) <= max_chars:
            return compact
        return compact[: max(0, max_chars - 1)].rstrip() + "..."

    def _build_scene_brief_text(self, scene: dict[str, Any]) -> str:
        summary = self._clip_text(str(scene.get("scene_summary") or ""), max_chars=500)
        goal = self._clip_text(str(scene.get("scene_goal") or ""), max_chars=350)
        milestones = self._normalize_title_list(scene.get("milestones", []), max_items=5, max_chars=140)
        entities = self._normalize_text_list(scene.get("related_entities", []), max_items=8, max_chars=80)
        lines = [
            f"Scene name: {str(scene.get('name') or scene.get('scene_id') or 'Scene')}",
            f"Scene summary: {summary or '(none)'}",
            f"Scene goal: {goal or '(none)'}",
            f"Milestones: {', '.join(milestones) if milestones else '(none)'}",
            f"Related entities: {', '.join(entities) if entities else '(none)'}",
        ]
        return "\n".join(lines)

    def _build_elder_context_text(self, buckets: Any) -> str:
        if not isinstance(buckets, dict):
            return ""
        ordered_keys = (
            "prior_events",
            "relationship_summaries",
            "personality_reminders",
            "unresolved_tensions",
            "style_details",
            "contradiction_warnings",
        )
        lines: list[str] = []
        for key in ordered_keys:
            values = buckets.get(key)
            if not isinstance(values, list):
                continue
            for value in values[:4]:
                compact = self._clip_text(str(value), max_chars=180)
                if compact:
                    lines.append(f"- {compact}")
        return "\n".join(lines[:16])

    def _build_intent_brief_text(self, intent: dict[str, Any]) -> str:
        if not isinstance(intent, dict):
            return ""
        lines: list[str] = []
        mapping = {
            "what_happens": "What happens",
            "emotional_progression": "Emotional progression",
            "speaking_goals": "Speaking goals",
            "implied_history": "Implied history",
            "forbidden_contradictions": "Forbidden contradictions",
        }
        for key, label in mapping.items():
            values = self._normalize_text_list(intent.get(key, []), max_items=6, max_chars=150)
            if values:
                lines.append(f"{label}: {', '.join(values)}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_single_paragraph(text: str) -> str:
        raw = re.sub(r"<[^>]+>", " ", str(text or ""))
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]
        paragraph = " ".join(sentences).strip() if sentences else raw
        return paragraph

    @staticmethod
    def _extract_paragraph_and_dialogue(html: str) -> tuple[str, str]:
        paragraph_match = re.search(r"<p[^>]*>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
        dialogue_match = re.search(
            r"<blockquote[^>]*>(.*?)</blockquote>", html, flags=re.IGNORECASE | re.DOTALL
        )
        paragraph = paragraph_match.group(1).strip() if paragraph_match else ""
        dialogue = dialogue_match.group(1).strip() if dialogue_match else ""
        return paragraph, dialogue

    def _limit_scene_prose_html(self, html: str, *, max_chars: int) -> str:
        paragraph, dialogue = self._extract_paragraph_and_dialogue(html)
        if not paragraph and not dialogue:
            fallback = self._clip_text(re.sub(r"<[^>]+>", " ", html or ""), max_chars=max_chars)
            return f"<p>{fallback}</p>" if fallback else ""

        paragraph = self._clip_text(paragraph, max_chars=max_chars)
        remaining = max(80, max_chars - len(paragraph))
        dialogue = self._clip_text(dialogue, max_chars=remaining) if dialogue else ""

        if dialogue:
            return f"<p>{paragraph}</p>\n<blockquote>{dialogue}</blockquote>"
        return f"<p>{paragraph}</p>"

    @staticmethod
    def _ensure_readable_html(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        # If model returned HTML-like content, normalize it to flat allowed blocks.
        if "<" in raw and ">" in raw:
            # Convert nested/inline tags into plain text blocks, keeping only p/blockquote/h1 wrappers.
            lowered = raw.lower()
            allowed_pattern = re.compile(
                r"<(h1|p|blockquote)[^>]*>(.*?)</\1>",
                flags=re.IGNORECASE | re.DOTALL,
            )
            blocks: list[tuple[str, str]] = []
            for match in allowed_pattern.finditer(raw):
                tag = str(match.group(1) or "").lower()
                content = str(match.group(2) or "")
                # Flatten nested tags to text.
                content = re.sub(r"<[^>]+>", " ", content)
                content = re.sub(r"\s+", " ", content).strip()
                if content:
                    blocks.append((tag, content))

            # Fallback: if no proper wrappers parsed, treat full HTML as plain text.
            if not blocks and any(tag in lowered for tag in ("<p", "<blockquote", "<h1", "<h2", "<h3", "<h4", "<div")):
                flattened = re.sub(r"<[^>]+>", " ", raw)
                flattened = re.sub(r"\s+", " ", flattened).strip()
                return f"<p>{flattened}</p>" if flattened else ""

            if blocks:
                html_blocks: list[str] = []
                for tag, content in blocks:
                    if tag == "h1":
                        html_blocks.append(f"<h1>{content}</h1>")
                    elif tag == "blockquote":
                        html_blocks.append(f"<blockquote>{content}</blockquote>")
                    else:
                        html_blocks.append(f"<p>{content}</p>")
                return "\n".join(html_blocks)

        chunks = [c.strip() for c in re.split(r"\n\s*\n+", raw) if c.strip()]
        if len(chunks) <= 1:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]
            if not sentences:
                return f"<p>{raw}</p>"
            chunks = []
            for i in range(0, len(sentences), 2):
                chunks.append(" ".join(sentences[i : i + 2]).strip())

        html_blocks: list[str] = []
        for chunk in chunks[:2]:
            is_dialogue = (
                chunk.startswith('"')
                or chunk.startswith("'")
                or chunk.startswith("“")
                or chunk.startswith("—")
                or chunk.startswith("- ")
            )
            if is_dialogue:
                html_blocks.append(f"<blockquote>{chunk}</blockquote>")
            else:
                html_blocks.append(f"<p>{chunk}</p>")
        return "\n".join(html_blocks)
