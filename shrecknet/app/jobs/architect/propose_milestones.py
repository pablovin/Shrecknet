"""
Milestone proposal extraction for Architect pipeline.
Callable method to propose milestones from entity and scene proposals.
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any, List, Dict
from uuid import uuid4
from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.prompts import ARCHITECT_SCENE_MILESTONE_PROPOSAL_PROMPT
from app.tasks.architect_analysis import _write_json_to_output_dirs, _resolve_analysis_output_dirs
from app.jobs.architect.schemas import SceneMilestoneProposal, SceneMilestoneProposalResponse

logger = logging.getLogger(__name__)

async def propose_milestones(
    run_id: str,
    entity_proposals: List[Dict[str, Any]],
    scene_proposals: List[Dict[str, Any]],
    author_id: str,
    output_job_name: str,
    llm_client: OpenAIClient,
    model_policy: ModelPolicy,
    max_concurrency: int = 10,
) -> Dict[str, Any]:
    """
    Propose milestones for each scene/entity using LLM, attach author/derived_from, and write artifact.
    """
    started = perf_counter()
    semaphore = asyncio.Semaphore(max_concurrency)
    milestones_by_scene = {}
    milestone_count = 0
    tasks = []

    # Build a lookup for entities present in each scene
    scene_entities = {}
    for scene in scene_proposals:
        scene_ref = scene.get("scene_ref")
        scene_entities[scene_ref] = set()
    for entity in entity_proposals:
        for scene_ref in entity.get("scene_refs", []):
            scene_entities.setdefault(scene_ref, set()).add(entity["canonical"])

    async def process_scene(scene: Dict[str, Any]):
        async with semaphore:
            scene_ref = scene["scene_ref"]
            scene_name = scene["name"]
            scene_desc = scene["description"]
            entities = list(scene_entities.get(scene_ref, []))
            prompt = ARCHITECT_SCENE_MILESTONE_PROPOSAL_PROMPT.format(
                known_aliases=", ".join(entities),
                chunk_dump=scene_desc,
            )
            model = model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)
            response = await llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            parsed = SceneMilestoneProposalResponse.model_validate_json(response)
            milestones = []
            for order, milestone in enumerate(parsed.scenes[0].milestones, start=1):
                milestone_ref = f"milestone-{uuid4()}"
                milestone_title = getattr(milestone, "title", None) or getattr(milestone, "label", "")
                milestones.append({
                    "milestone_ref": milestone_ref,
                    "scene_ref": scene_ref,
                    "scene_id": scene.get("scene_id"),
                    "title": milestone_title,
                    "description": milestone.description,
                    "boundary_type": milestone.boundary_type,
                    "mentions": milestone.mentions,
                    "milestone_order": order,
                    "author": {
                        "created_by_type": "agent",
                        "created_by_author": author_id,
                    },
                    "derived_from": {
                        "entity_instance_id": run_id,
                    },
                })
            # Ensure at least 2 milestones (begin/end)
            if len(milestones) < 2:
                milestones = [
                    {**milestones[0], "boundary_type": "begin", "milestone_order": 1},
                    {**milestones[-1], "boundary_type": "end", "milestone_order": 2},
                ]
            milestones_by_scene[scene_ref] = milestones
            return scene_ref, milestones

    for scene in scene_proposals:
        tasks.append(process_scene(scene))
    results = await asyncio.gather(*tasks)
    all_milestones = []
    for scene_ref, milestones in results:
        all_milestones.extend(milestones)
        logger.info(f"Scene {scene_ref}: {len(milestones)} milestones proposed.")
    milestone_count = len(all_milestones)
    elapsed = round(perf_counter() - started, 3)
    logger.info(f"Total milestones proposed: {milestone_count}, elapsed_seconds={elapsed}")
    # Write artifact
    output_dirs = _resolve_analysis_output_dirs(output_job_name)
    _write_json_to_output_dirs(
        output_dirs=output_dirs,
        filename="proposed_milestones.json",
        payload={
            "run_id": run_id,
            "milestone_count": milestone_count,
            "elapsed_seconds": elapsed,
            "milestones": all_milestones,
        },
    )
    return {
        "milestone_count": milestone_count,
        "elapsed_seconds": elapsed,
        "milestones": all_milestones,
    }
