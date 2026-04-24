"""Scene-centric Novelist orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.prompts import (
    ARCHITECT_ENTITY_PROPOSAL_PROMPT,
    ARECHITECT_MILESTONE_PROPOSAL_PROMPT,
)
from app.jobs.architect.scene_centric_chunking import (
    build_scene_chunks,
    extract_paragraphs_from_sources,
    segment_chunk_into_scenes,
)
from app.jobs.architect.schemas import ChunkExtractionResponse
from app.jobs.novelist.prompts import (
    CONTINUITY_BRIEF_PROMPT,
    NOVELIST_ELDER_QUERY_PROMPT,
    NOVELIST_SCAFFOLD_NORMALIZATION_PROMPT,
    NOVELIST_SCENE_CRITIC_PROMPT,
    NOVELIST_SCENE_INTENT_PROMPT,
    NOVELIST_SCENE_PACKAGE_PROMPT,
    NOVELIST_SCENE_PROSE_PROMPT,
    NOVELIST_SCENE_REVISION_PROMPT,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

logger = logging.getLogger(__name__)

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]
ElderQueryRunner = Callable[[Agent, str], Awaitable[list[dict[str, Any]]]]
ArchitectScaffoldingRunner = Callable[
    [Agent, str, str | None], Awaitable[dict[str, Any]]
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
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        max_concurrency: int = 10,
        elder_query_runner: ElderQueryRunner | None = None,
        architect_scaffolding_runner: ArchitectScaffoldingRunner | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.draft_model = getattr(model_policy, "model_novelist_draft", None)
        self.critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.max_concurrency = max(2, min(4, max_concurrency))
        self._scene_prose_max_chars = 1400
        self._critic_input_max_chars = 110_000
        self._revision_input_max_chars = 110_000
        self.elder_query_runner = elder_query_runner
        self.architect_scaffolding_runner = architect_scaffolding_runner

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
        previous_session_text = (payload.previous_session_text or "").strip()

        continuity_brief = (payload.previous_session_summary or "").strip()
        if not continuity_brief:
            continuity_brief = await self._build_continuity_brief(
                previous_session_text=previous_session_text,
                language=language,
                instructions=instructions,
                conversation_id=conversation_id,
            )

        artifacts: dict[str, Any] = {
            "inputs": {
                "unstructured_text": payload.unstructured_text,
                "language": payload.language,
                "instructions": payload.instructions,
                "previous_session_id": payload.previous_session_id,
                "continuity_brief": continuity_brief,
                "previous_session_summary": continuity_brief,
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
        scaffolding_t0 = time.monotonic()
        scaffolding = await self._build_scaffolding(
            agent=agent,
            unstructured_text=unstructured_text,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
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

        # Stage: scene package extraction
        package_t0 = time.monotonic()
        scene_packages = await self._build_scene_packages(
            scenes=scaffolding.get("scenes", []),
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["scene_package"] = {
            "count": len(scene_packages),
            "packages": scene_packages,
        }
        artifacts["timings_ms"]["scene_package"] = round(
            (time.monotonic() - package_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.SCENE_PACKAGE,
                {
                    "artifacts": artifacts,
                    "scene_count": len(scene_packages),
                },
            )

        # Stage: Elder retrieval
        retrieval_t0 = time.monotonic()
        retrieval_by_scene = await self._collect_scene_retrieval(
            agent=agent,
            scene_packages=scene_packages,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["retrieval"] = retrieval_by_scene
        artifacts["timings_ms"]["retrieval"] = round(
            (time.monotonic() - retrieval_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.RETRIEVAL,
                {
                    "artifacts": artifacts,
                    "scene_count": len(scene_packages),
                },
            )

        # Stage: scene intent drafting
        intent_t0 = time.monotonic()
        intents_by_scene = await self._draft_scene_intents(
            scene_packages=scene_packages,
            retrieval_by_scene=retrieval_by_scene,
            continuity_brief=continuity_brief,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["intent_drafting"] = intents_by_scene
        artifacts["timings_ms"]["intent_drafting"] = round(
            (time.monotonic() - intent_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.INTENT_DRAFTING,
                {
                    "artifacts": artifacts,
                    "scene_count": len(scene_packages),
                },
            )

        # Stage: prose generation
        prose_t0 = time.monotonic()
        prose_by_scene = await self._generate_scene_prose(
            agent=agent,
            scene_packages=scene_packages,
            intents_by_scene=intents_by_scene,
            retrieval_by_scene=retrieval_by_scene,
            continuity_brief=continuity_brief,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["prose_generation"] = prose_by_scene
        artifacts["timings_ms"]["prose_generation"] = round(
            (time.monotonic() - prose_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.PROSE_GENERATION,
                {
                    "artifacts": artifacts,
                    "draft_text": self._merge_scene_html(prose_by_scene),
                },
            )

        # Stage: critic
        critic_t0 = time.monotonic()
        critic = await self._critic_scene_set(
            scene_packages=scene_packages,
            prose_by_scene=prose_by_scene,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["critic"] = critic
        artifacts["timings_ms"]["critic"] = round(
            (time.monotonic() - critic_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {
                    "artifacts": artifacts,
                    "critic_notes": critic,
                },
            )

        # Stage: revision
        revision_t0 = time.monotonic()
        revision = await self._revise_scene_set(
            scene_packages=scene_packages,
            prose_by_scene=prose_by_scene,
            critic=critic,
            language=language,
            instructions=instructions,
            conversation_id=conversation_id,
        )
        artifacts["stages"]["revision"] = revision
        artifacts["timings_ms"]["revision"] = round(
            (time.monotonic() - revision_t0) * 1000, 2
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.REVISION,
                {
                    "artifacts": artifacts,
                    "critic_notes": critic,
                },
            )

        # Stage: merging
        merge_t0 = time.monotonic()
        revised_scenes = revision.get("scenes", []) if isinstance(revision, dict) else []
        if not revised_scenes:
            revised_scenes = [
                {
                    "scene_id": item.get("scene_id"),
                    "name": item.get("name") or item.get("scene_summary") or item.get("scene_id"),
                    "prose_html": item.get("prose_html", ""),
                    "merged_from": [item.get("scene_id")],
                    "split_from": None,
                    "notes": [],
                }
                for item in prose_by_scene
            ]
        final_html = self._merge_revised_scene_html(revised_scenes)
        artifacts["stages"]["merging"] = {
            "scene_count": len(revised_scenes),
            "final_text": final_html,
        }
        artifacts["timings_ms"]["merging"] = round((time.monotonic() - merge_t0) * 1000, 2)

        scene_results = self._build_scene_results(
            scene_packages=scene_packages,
            retrieval_by_scene=retrieval_by_scene,
            intents_by_scene=intents_by_scene,
            prose_by_scene=prose_by_scene,
            critic=critic,
            revision=revision,
        )

        artifacts["scene_progress"] = {
            item["scene_id"]: {
                "intent_done": bool(item.get("intent")),
                "prose_done": bool(item.get("prose_html")),
                "critic_issue_count": item.get("critic_issue_count", 0),
                "revision_action": item.get("revision_action", "kept"),
            }
            for item in scene_results
        }
        artifacts["timings_ms"]["total"] = round((time.monotonic() - started_total) * 1000, 2)

        timing_summary = {
            "total_ms": artifacts["timings_ms"].get("total", 0.0),
            "by_stage_ms": artifacts["timings_ms"],
            "scene_count": len(scene_results),
            "retrieval_query_count": sum(
                len((retrieval_by_scene.get(item.get("scene_id", ""), {}) or {}).get("queries", []))
                for item in scene_packages
            ),
        }
        step_outputs = self._build_step_outputs(
            scaffolding=scaffolding,
            scene_packages=scene_packages,
            retrieval_by_scene=retrieval_by_scene,
            intents_by_scene=intents_by_scene,
            prose_by_scene=prose_by_scene,
            critic=critic,
            revision=revision,
            final_html=final_html,
        )
        artifacts["step_outputs"] = step_outputs

        if stage_callback:
            await stage_callback(
                NovelistStage.MERGING,
                {
                    "artifacts": artifacts,
                    "draft_text": final_html,
                    "critic_notes": critic,
                    "scene_results": scene_results,
                    "timing_summary": timing_summary,
                },
            )

        return {
            "artifacts": artifacts,
            "draft_text": final_html,
            "critic_notes": json.dumps(critic, ensure_ascii=True),
            "scene_results": scene_results,
            "timing_summary": timing_summary,
            "step_outputs": step_outputs,
        }

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
                    conversation_id,
                )
                if isinstance(shared, dict) and isinstance(shared.get("scenes"), list):
                    return shared
                logger.warning("architect_scaffolding_runner returned invalid payload")
            except Exception:
                logger.warning(
                    "architect_scaffolding_runner_failed_fallback_to_local",
                    exc_info=True,
                )

        model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)
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
                        "start_paragraph": 1,
                        "end_paragraph": chunk.paragraph_count,
                        "text": "\n".join(
                            [
                                f"[P{idx}] {paragraph}"
                                for idx, paragraph in enumerate(chunk.paragraphs, start=1)
                            ]
                        ),
                    }
                ]

            for local_scene in scenes:
                abs_start = chunk.paragraph_start + int(local_scene.get("start_paragraph", 1)) - 1
                abs_end = chunk.paragraph_start + int(local_scene.get("end_paragraph", 1)) - 1
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

        ontology_definitions = self._serialize_ontology_definitions(agent)
        enriched_scenes = await self._extract_scene_entities_and_milestones(
            scenes=segmented_scenes,
            ontology_definitions=ontology_definitions,
            model=model,
            conversation_id=conversation_id,
        )

        normalize_system = self._compose_system_prompt(
            NOVELIST_SCAFFOLD_NORMALIZATION_PROMPT,
            language=language,
            instructions=instructions,
        )
        normalize_user = json.dumps({"scenes": enriched_scenes}, ensure_ascii=True)
        normalize_raw, normalize_ms = await self._call_llm(
            model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
            messages=[
                {"role": "system", "content": normalize_system},
                {"role": "user", "content": normalize_user},
            ],
            temperature=0.1,
            conversation_id=conversation_id,
        )
        normalized = self._parse_json_object(normalize_raw) or {}
        normalized_scenes = normalized.get("scenes") if isinstance(normalized, dict) else None
        if not isinstance(normalized_scenes, list) or not normalized_scenes:
            normalized_scenes = [
                {
                    "scene_id": scene["scene_id"],
                    "name": scene["name"],
                    "scene_summary": scene.get("scene_summary", ""),
                    "milestones": scene.get("milestones", []),
                    "related_entities": scene.get("related_entities", []),
                    "source_anchors": scene.get("source_anchors", []),
                    "new_or_update": "new",
                    "source_paragraphs": scene.get("source_paragraphs", []),
                    "raw_scene_text": scene.get("raw_scene_text", ""),
                }
                for scene in enriched_scenes
            ]

        by_id: dict[str, dict[str, Any]] = {scene["scene_id"]: scene for scene in enriched_scenes}
        final_scenes: list[dict[str, Any]] = []
        for idx, scene in enumerate(normalized_scenes, start=1):
            scene_id = str(scene.get("scene_id") or f"scene-{idx:03d}").strip()
            source = by_id.get(scene_id, {})
            final_scenes.append(
                {
                    "scene_id": scene_id,
                    "name": str(scene.get("name") or source.get("name") or scene_id).strip(),
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
                    "source_anchors": self._normalize_text_list(
                        scene.get("source_anchors") or source.get("source_anchors") or [], max_items=8
                    ),
                    "new_or_update": str(scene.get("new_or_update") or "new").strip().lower() or "new",
                    "source_paragraphs": source.get("source_paragraphs", []),
                    "raw_scene_text": source.get("raw_scene_text", ""),
                }
            )

        return {
            "model": model,
            "normalize_model": self.critic_model
            or self.model_policy.get_model(LLMTask.VALIDATION),
            "normalize_latency_ms": normalize_ms,
            "normalize_raw": normalize_raw,
            "scene_count": len(final_scenes),
            "scenes": final_scenes,
        }

    async def _extract_scene_entities_and_milestones(
        self,
        *,
        scenes: list[dict[str, Any]],
        ontology_definitions: str,
        model: str,
        conversation_id: str | None,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run(scene: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                scene_name = str(scene.get("name") or scene.get("scene_id") or "Scene").strip()
                scene_summary = str(scene.get("scene_summary") or "").strip()
                raw_scene_text = str(scene.get("raw_scene_text") or "").strip()

                entity_prompt = ARCHITECT_ENTITY_PROPOSAL_PROMPT.format(
                    ontology_definitions=ontology_definitions,
                    scene_name=scene_name,
                    scene_description=scene_summary or "(no description)",
                    scene_text=raw_scene_text or "(no text)",
                )
                entity_raw, _ = await self._call_llm(
                    model=model,
                    messages=[{"role": "user", "content": entity_prompt}],
                    temperature=0.1,
                    conversation_id=conversation_id,
                )
                entity_payload = self._parse_json_object(entity_raw) or {}
                entities = self._parse_architect_entities(entity_payload)

                milestone_prompt = ARECHITECT_MILESTONE_PROPOSAL_PROMPT.format(
                    scene_ref=scene.get("scene_id", "scene"),
                    scene_name=scene_name,
                    scene_description=scene_summary or "(no description)",
                    scene_entities="\n".join(f"- {name}" for name in entities) or "(none)",
                    scene_text=raw_scene_text or "(no text)",
                )
                milestone_raw, _ = await self._call_llm(
                    model=model,
                    messages=[{"role": "user", "content": milestone_prompt}],
                    temperature=0.1,
                    conversation_id=conversation_id,
                )
                milestone_payload = self._parse_json_object(milestone_raw) or {}
                milestones = self._parse_milestones(milestone_payload)

                return {
                    **scene,
                    "related_entities": entities,
                    "milestones": milestones,
                    "entity_raw": entity_raw,
                    "milestone_raw": milestone_raw,
                }

        return await asyncio.gather(*[asyncio.create_task(_run(scene)) for scene in scenes])

    async def _build_scene_packages(
        self,
        *,
        scenes: list[dict[str, Any]],
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> list[dict[str, Any]]:
        if not scenes:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)
        system_prompt = self._compose_system_prompt(
            NOVELIST_SCENE_PACKAGE_PROMPT,
            language=language,
            instructions=instructions,
        )

        async def _run(scene: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                user_prompt = json.dumps({"scenes": [scene]}, ensure_ascii=True)
                raw, latency_ms = await self._call_llm(
                    model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    conversation_id=conversation_id,
                )
                parsed = self._parse_json_object(raw) or {}
                packages = parsed.get("scene_packages") if isinstance(parsed, dict) else None
                package = {}
                if isinstance(packages, list) and packages:
                    candidate = packages[0]
                    package = candidate if isinstance(candidate, dict) else {}
                if not package:
                    package = {
                        "scene_id": scene.get("scene_id"),
                        "source_paragraphs": scene.get("source_paragraphs", []),
                        "raw_scene_text": scene.get("raw_scene_text", ""),
                        "scene_summary": scene.get("scene_summary", ""),
                        "scene_goal": scene.get("scene_summary", ""),
                        "milestones": scene.get("milestones", []),
                        "related_entities": scene.get("related_entities", []),
                        "temporal_position_hint": "middle",
                        "tone_hint": "dramatic",
                        "open_questions_for_retrieval": [
                            f"What prior event most affects {scene.get('name', 'this scene')}?"
                        ],
                    }
                package["scene_id"] = str(package.get("scene_id") or scene.get("scene_id"))
                package.setdefault("source_paragraphs", scene.get("source_paragraphs", []))
                package.setdefault("raw_scene_text", scene.get("raw_scene_text", ""))
                package.setdefault("scene_summary", scene.get("scene_summary", ""))
                package.setdefault("milestones", scene.get("milestones", []))
                package.setdefault("related_entities", scene.get("related_entities", []))
                package.setdefault("new_or_update", scene.get("new_or_update", "new"))
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
    ) -> dict[str, dict[str, Any]]:
        if not self.elder_query_runner:
            return {
                str(scene.get("scene_id")): {
                    "queries": [],
                    "buckets": self._empty_retrieval_buckets(),
                    "raw_sources": [],
                }
                for scene in scene_packages
            }

        query_system = self._compose_system_prompt(
            NOVELIST_ELDER_QUERY_PROMPT,
            language=language,
            instructions=instructions,
        )

        async def _generate_queries(scene: dict[str, Any]) -> tuple[str, list[str], str]:
            scene_id = str(scene.get("scene_id") or "")
            scene_brief = self._build_scene_brief_text(scene)
            user_prompt = (
                f"Scene ID: {scene_id or 'scene'}\n"
                f"{scene_brief}\n\n"
                "Return JSON with 2-4 short continuity-focused retrieval questions."
            )
            raw, _ = await self._call_llm(
                model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                messages=[
                    {"role": "system", "content": query_system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                conversation_id=conversation_id,
            )
            payload = self._parse_json_object(raw) or {}
            queries = self._normalize_text_list(payload.get("queries", []), max_items=4)
            if not queries:
                queries = self._normalize_text_list(
                    scene.get("open_questions_for_retrieval", []), max_items=4
                )
            return str(scene.get("scene_id")), queries, raw

        query_results = await asyncio.gather(
            *[asyncio.create_task(_generate_queries(scene)) for scene in scene_packages]
        )

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _fetch(scene_id: str, query: str) -> tuple[str, str, list[dict[str, Any]]]:
            async with semaphore:
                try:
                    raw_sources = await self.elder_query_runner(agent, query)
                except Exception:
                    logger.warning("scene_retrieval_failed scene=%s query=%s", scene_id, query, exc_info=True)
                    raw_sources = []
                return scene_id, query, [s for s in raw_sources if isinstance(s, dict)]

        fetch_tasks = [
            asyncio.create_task(_fetch(scene_id, query))
            for scene_id, queries, _ in query_results
            for query in queries
        ]
        fetched = await asyncio.gather(*fetch_tasks) if fetch_tasks else []

        grouped: dict[str, dict[str, Any]] = {
            scene_id: {
                "queries": queries,
                "query_plan_raw": query_raw,
                "raw_sources": [],
                "buckets": self._empty_retrieval_buckets(),
            }
            for scene_id, queries, query_raw in query_results
        }

        for scene_id, _query, sources in fetched:
            grouped.setdefault(
                scene_id,
                {
                    "queries": [],
                    "query_plan_raw": "",
                    "raw_sources": [],
                    "buckets": self._empty_retrieval_buckets(),
                },
            )
            grouped[scene_id]["raw_sources"].extend(sources)

        for scene_id, payload in grouped.items():
            payload["buckets"] = self._filter_scene_retrieval(payload.get("raw_sources", []))
            payload["bucket_counts"] = {
                key: len(values) for key, values in payload["buckets"].items()
            }

        return grouped

    async def _draft_scene_intents(
        self,
        *,
        scene_packages: list[dict[str, Any]],
        retrieval_by_scene: dict[str, dict[str, Any]],
        continuity_brief: str,
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> dict[str, dict[str, Any]]:
        if not scene_packages:
            return {}

        semaphore = asyncio.Semaphore(self.max_concurrency)
        system_prompt = self._compose_system_prompt(
            NOVELIST_SCENE_INTENT_PROMPT,
            language=language,
            instructions=instructions,
        )

        async def _run(scene: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                scene_id = str(scene.get("scene_id"))
                retrieval = retrieval_by_scene.get(scene_id, {})
                scene_brief = self._build_scene_brief_text(scene)
                elder_brief = self._build_elder_context_text(retrieval.get("buckets", {}))
                user_payload = (
                    f"Scene ID: {scene_id}\n"
                    f"{scene_brief}\n\n"
                    f"Continuity brief:\n{continuity_brief or '(none)'}\n\n"
                    f"Elder context:\n{elder_brief or '(none)'}\n\n"
                    "Return JSON in the required schema."
                )
                raw, latency_ms = await self._call_llm(
                    model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                    temperature=0.2,
                    conversation_id=conversation_id,
                )
                parsed = self._parse_json_object(raw) or {}
                if not isinstance(parsed, dict):
                    parsed = {}
                parsed.setdefault("scene_id", scene_id)
                parsed.setdefault("what_happens", [scene.get("scene_goal") or scene.get("scene_summary") or ""]) 
                parsed.setdefault("emotional_progression", [])
                parsed.setdefault("speaking_goals", [])
                parsed.setdefault("implied_history", [])
                parsed.setdefault("forbidden_contradictions", [])
                parsed["latency_ms"] = latency_ms
                return scene_id, parsed

        pairs = await asyncio.gather(*[asyncio.create_task(_run(scene)) for scene in scene_packages])
        return {scene_id: payload for scene_id, payload in pairs}

    async def _generate_scene_prose(
        self,
        *,
        agent: Agent,
        scene_packages: list[dict[str, Any]],
        intents_by_scene: dict[str, dict[str, Any]],
        retrieval_by_scene: dict[str, dict[str, Any]],
        continuity_brief: str,
        language: str,
        instructions: str,
        conversation_id: str | None,
    ) -> list[dict[str, Any]]:
        if not scene_packages:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)
        system_prompt = self._compose_system_prompt(
            NOVELIST_SCENE_PROSE_PROMPT,
            language=language,
            instructions=instructions,
        )
        style_hint = str(agent.writing_style or "").strip()

        async def _run(scene: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                scene_id = str(scene.get("scene_id"))
                intent = intents_by_scene.get(scene_id, {})
                retrieval = retrieval_by_scene.get(scene_id, {})
                scene_brief = self._build_scene_brief_text(scene)
                intent_brief = self._build_intent_brief_text(intent)
                elder_brief = self._build_elder_context_text(retrieval.get("buckets", {}))
                user_payload = (
                    f"Scene ID: {scene_id}\n"
                    f"{scene_brief}\n\n"
                    f"Intent:\n{intent_brief or '(none)'}\n\n"
                    f"Elder context:\n{elder_brief or '(none)'}\n\n"
                    f"Continuity brief:\n{continuity_brief or '(none)'}\n\n"
                    f"Writer style:\n{style_hint or '(none)'}\n\n"
                    "Write only the scene prose."
                )
                raw, latency_ms = await self._call_llm(
                    model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                    temperature=0.25,
                    conversation_id=conversation_id,
                )
                html = self._ensure_readable_html(raw)
                html = self._limit_scene_prose_html(html, max_chars=self._scene_prose_max_chars)
                return {
                    "scene_id": scene_id,
                    "name": scene.get("name") or scene.get("scene_summary") or scene_id,
                    "scene_summary": scene.get("scene_summary", ""),
                    "prose_html": html,
                    "latency_ms": latency_ms,
                }

        out = await asyncio.gather(*[asyncio.create_task(_run(scene)) for scene in scene_packages])
        order = {str(scene.get("scene_id")): idx for idx, scene in enumerate(scene_packages)}
        out.sort(key=lambda item: order.get(str(item.get("scene_id")), 0))
        return out

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
            NOVELIST_SCENE_CRITIC_PROMPT,
            language=language,
            instructions=instructions,
        )
        merged_text = self._merge_scene_html(prose_by_scene)
        merged_text = self._clip_text(merged_text, max_chars=self._critic_input_max_chars)
        user_payload = (
            "Full draft text to critique:\n"
            f"{merged_text}\n\n"
            "Return only concise critique notes in JSON using the requested schema."
        )
        raw, latency_ms = await self._call_llm(
            model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.1,
            conversation_id=conversation_id,
        )
        parsed = self._parse_json_object(raw) or {}
        by_scene = parsed.get("by_scene") if isinstance(parsed, dict) else None
        if not isinstance(by_scene, dict):
            by_scene = {}
        normalized_by_scene: dict[str, dict[str, list[str]]] = {}
        for scene in scene_packages:
            scene_id = str(scene.get("scene_id"))
            row = by_scene.get(scene_id) if isinstance(by_scene.get(scene_id), dict) else {}
            normalized_by_scene[scene_id] = {
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
        system_prompt = self._compose_system_prompt(
            NOVELIST_SCENE_REVISION_PROMPT,
            language=language,
            instructions=instructions,
        )
        merged_text = self._merge_scene_html(prose_by_scene)
        merged_text = self._clip_text(merged_text, max_chars=self._revision_input_max_chars)
        critic_text = self._critic_to_text(critic)
        user_payload = (
            "Draft text to revise:\n"
            f"{merged_text}\n\n"
            "Critic notes:\n"
            f"{critic_text or '(none)'}\n\n"
            "Return revised prose HTML only."
        )
        raw, latency_ms = await self._call_llm(
            model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            temperature=0.2,
            conversation_id=conversation_id,
        )
        revised_html = self._ensure_readable_html(raw)
        revised_html = self._limit_scene_prose_html(revised_html, max_chars=self._revision_input_max_chars)
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
            "latency_ms": latency_ms,
        }

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
            title = str(item.get("title") or item.get("label") or "").strip()
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
            prose = prose_map.get(scene_id, {})
            critic_row = critic_by_scene.get(scene_id, {}) if isinstance(critic_by_scene, dict) else {}
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
                    "source_paragraphs": scene.get("source_paragraphs", []),
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
                    "milestones": scene.get("milestones", []),
                    "related_entities": scene.get("related_entities", []),
                    "source_anchors": scene.get("source_anchors", []),
                    "new_or_update": scene.get("new_or_update", "new"),
                }
            )

        step_3_context: list[dict[str, Any]] = []
        for scene_id in ordered_scene_ids:
            retrieval = retrieval_by_scene.get(scene_id, {})
            step_3_context.append(
                {
                    "scene_id": scene_id,
                    "queries": retrieval.get("queries", []),
                    "narrative_context": retrieval.get("buckets", {}),
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
            blocks.append(f"<h2>Scene {idx}: {title}</h2>\n{html}")
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _merge_revised_scene_html(revised_scenes: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for idx, item in enumerate(revised_scenes, start=1):
            title = str(item.get("name") or item.get("scene_id") or f"Scene {idx}").strip()
            html = str(item.get("prose_html") or "").strip()
            blocks.append(f"<h2>Scene {idx}: {title}</h2>\n{html}")
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

    async def _build_continuity_brief(
        self,
        *,
        previous_session_text: str,
        language: str,
        instructions: str,
        conversation_id: str | None = None,
    ) -> str:
        raw = re.sub(r"<[^>]+>", " ", previous_session_text or "")
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            return ""
        system_prompt = self._compose_system_prompt(
            CONTINUITY_BRIEF_PROMPT, language=language, instructions=instructions
        )
        user_prompt = (
            "Previous session text:\n"
            f"{raw}\n\n"
            "Return 5-8 short lines only."
        )
        try:
            continuity_raw, _ = await self._call_llm(
                model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.warning("Failed to generate continuity brief via LLM", exc_info=True)
            return ""
        return self._normalize_continuity_brief(continuity_raw)

    @staticmethod
    def _normalize_continuity_brief(raw_brief: str) -> str:
        lines: list[str] = []
        for row in (raw_brief or "").splitlines():
            cleaned = re.sub(r"^\s*[-*0-9.)\s]+", "", row).strip()
            if not cleaned:
                continue
            lines.append(f"- {cleaned[:220]}")
        if not lines:
            compact = re.sub(r"\s+", " ", raw_brief or "").strip()
            if compact:
                lines = [f"- {compact[:220]}"]
        return "\n".join(lines[:8])

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
    def _parse_json_object(raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception:
            # Try extracting first JSON object from mixed output.
            match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except Exception:
                return None

    async def _call_llm(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        conversation_id: str | None,
    ) -> tuple[str, float]:
        started = time.monotonic()
        try:
            raw = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                conversation_id=conversation_id,
                use_conversation_memory=False,
            )
        except TypeError:
            raw = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=temperature,
                conversation_id=conversation_id,
            )
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        return raw, elapsed_ms

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
        milestones = self._normalize_text_list(scene.get("milestones", []), max_items=5, max_chars=140)
        entities = self._normalize_text_list(scene.get("related_entities", []), max_items=8, max_chars=80)
        lines = [
            f"Scene name: {str(scene.get('name') or scene.get('scene_id') or 'Scene').strip()}",
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
    def _critic_to_text(critic: dict[str, Any]) -> str:
        if not isinstance(critic, dict):
            return ""
        lines: list[str] = []
        global_notes = critic.get("global_notes")
        if isinstance(global_notes, list):
            for note in global_notes[:12]:
                clean = re.sub(r"\s+", " ", str(note)).strip()
                if clean:
                    lines.append(f"- {clean}")

        by_scene = critic.get("by_scene")
        if isinstance(by_scene, dict):
            for scene_id, payload in by_scene.items():
                if not isinstance(payload, dict):
                    continue
                issue_lines: list[str] = []
                for key in (
                    "continuity_issues",
                    "duplication",
                    "missing_transitions",
                    "voice_drift",
                    "pacing",
                    "graph_contradictions",
                    "exposition_problems",
                ):
                    values = payload.get(key)
                    if isinstance(values, list):
                        for value in values[:2]:
                            clean = re.sub(r"\s+", " ", str(value)).strip()
                            if clean:
                                issue_lines.append(clean)
                if issue_lines:
                    lines.append(f"{scene_id}: {' | '.join(issue_lines[:5])}")
        return "\n".join(lines[:40])

    @staticmethod
    def _ensure_readable_html(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        if any(tag in raw.lower() for tag in ("<p", "<blockquote", "<ul", "<ol", "<h3", "<h4")):
            return raw

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
