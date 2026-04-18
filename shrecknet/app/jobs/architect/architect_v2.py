"""
New Architect Pipeline (V2) - Efficient and Scalable Entity Extraction.

This module implements a redesigned 4-step pipeline:
1. Chunk-level entity extraction (slim JSON)
2. Global deduplication across chunks
3. LLM-based reconciliation with existing entities
4. Map back to final JSON with resolved status
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable
from uuid import uuid4

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.architect.prompts import (
    ARCHITECT_CHUNK_EXTRACTION_PROMPT,
    ARCHITECT_RELATES_TO_PROPOSAL_PROMPT,
    ARCHITECT_RECONCILIATION_PROMPT,
    ARCHITECT_SCENE_MILESTONE_PROPOSAL_PROMPT,
)
from app.jobs.architect.schemas import (
    ChunkExtractionResponse,
    DedupedEntityProposal,
    ExistingNodeInfo,
    FinalEntityProposal,
    RelatesToProposalResponse,
    ReconciledNewEntity,
    ReconciliationResponse,
    SceneMilestoneProposalResponse,
)
from app.models.architect import ArchitectProposalType
from app.schemas.ontology_instance import OntologyInstanceRead

logger = logging.getLogger(__name__)

# Threshold for token overlap ratio when matching entity names
# At least this fraction of the smaller set's tokens must match
MIN_TOKEN_OVERLAP_RATIO = 0.5


@dataclass
class ChunkInput:
    """Container for a text chunk to analyze."""

    index: int
    entity_alias: str | None
    entity_definition_id: int | None
    text: str


class ArchitectOrchestratorV2:
    """
    Redesigned Architect pipeline that is more efficient and scalable.

    Pipeline steps:
    1. Extract entities from each chunk independently (slim JSON)
    2. Deduplicate entities across all chunks programmatically
    3. Reconcile with existing entities using LLM
    4. Map back to final JSON with resolved status
    """

    def __init__(
        self,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
        chunk_concurrency: int = 4,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunk_concurrency = max(1, chunk_concurrency)

    async def analyse(
        self,
        *,
        agent_ontology_ids: list[int],
        ontology_instance: OntologyInstanceRead,
        entity_definitions: list[dict[str, Any]],
        author_agent_id: str | None = None,
        override_chunk_size: int | None = None,
        override_max_chunks: int | None = None,
    ) -> dict[str, Any]:
        """
        Execute the complete 4-step pipeline.

        Returns:
            Dictionary containing proposals and metadata in the new format
        """
        # Step 0: Preload data
        logger.info("Step 0: Preloading node catalogue and ontology definitions")
        node_catalogue = await self._load_node_catalogue(agent_ontology_ids)
        ontology_definitions = self._format_ontology_definitions(entity_definitions)
        allowed_ontology_names = self._build_allowed_ontology_names(entity_definitions)

        # Build chunks
        chunks = self._build_chunks(
            ontology_instance,
            chunk_size=override_chunk_size or self.chunk_size,
            max_chunks=override_max_chunks,
        )
        logger.info(
            "architect_v2_chunks: instance=%s chunk_count=%d",
            ontology_instance.instance_id,
            len(chunks),
        )

        if not chunks:
            return {
                "proposals": [],
                "chunks": [],
                "chunk_count": 0,
                "pipeline_version": "v2",
            }

        primary_source_entity = self._select_primary_source_entity(ontology_instance)

        # Step 1 + 2 run in parallel:
        # A) Existing entity proposal workflow (behavior preserved)
        # B) Scene/Milestone proposal workflow
        entity_task = self._propose_entities(
            chunks=chunks,
            ontology_definitions=ontology_definitions,
            node_catalogue=node_catalogue,
            allowed_ontology_names=allowed_ontology_names,
        )
        scene_task = self._propose_scenes_milestones(
            chunks=chunks,
            known_entities=node_catalogue,
            source_entity_instance_id=primary_source_entity,
            author_id=author_agent_id or "architect-agent",
        )
        entity_result, scene_result = await asyncio.gather(entity_task, scene_task)
        logger.info(
            "Step 1B: Scene/Milestone extraction completed (scenes=%d milestones=%d)",
            scene_result["scene_count"],
            scene_result["milestone_count"],
        )

        # Step 3: resolve scene/milestone mentions against existing entities and
        # new entity proposals and emit final RELATES_TO proposal objects.
        logger.info("Step 3B: Resolving scene/milestone mentions into RELATES_TO")
        relates_to_proposals = await self._propose_relates_to_links(
            scene_and_milestone_proposals=scene_result["proposals"],
            entity_proposals=entity_result["proposals"],
            known_entities=node_catalogue,
        )
        logger.info(
            "Step 4B: Created %d RELATES_TO proposals",
            len(relates_to_proposals),
        )

        proposals = (
            entity_result["proposals"]
            + scene_result["proposals"]
            + relates_to_proposals
        )
        logger.info(
            "architect_v2_summary: entities=%d scenes=%d milestones=%d relates_to=%d total=%d",
            len(entity_result["proposals"]),
            scene_result["scene_count"],
            scene_result["milestone_count"],
            len(relates_to_proposals),
            len(proposals),
        )

        return {
            "proposals": proposals,
            "chunks": entity_result["chunks"],
            "chunk_count": len(chunks),
            "pipeline_version": "v2",
            "deduped_count": entity_result["deduped_count"],
            "existing_count": entity_result["existing_count"],
            "new_count": entity_result["new_count"],
            "scene_count": scene_result["scene_count"],
            "milestone_count": scene_result["milestone_count"],
            "relates_to_count": len(relates_to_proposals),
        }

    async def _propose_entities(
        self,
        *,
        chunks: list[ChunkInput],
        ontology_definitions: str,
        node_catalogue: list[ExistingNodeInfo],
        allowed_ontology_names: set[str],
    ) -> dict[str, Any]:
        """Keep the existing entity proposal flow as an atomic stage."""
        logger.info("Step 1A: Extracting entities from %d chunks", len(chunks))
        chunk_results = await self._extract_chunk_entities(chunks, ontology_definitions)

        logger.info("Step 2A: Deduplicating entities across chunks")
        deduped_entities = self._deduplicate_entities(chunk_results)
        logger.info("Deduplication: %d unique entities found", len(deduped_entities))

        logger.info("Step 3A: Reconciling with existing entities")
        reconciled = await self._reconcile_with_existing(
            deduped_entities,
            node_catalogue,
            ontology_definitions,
        )

        logger.info("Step 4A: Creating final entity proposals")
        proposals = self._create_final_proposals(
            chunk_results,
            deduped_entities,
            reconciled,
            allowed_ontology_names=allowed_ontology_names,
        )
        return {
            "proposals": proposals,
            "chunks": chunk_results,
            "deduped_count": len(deduped_entities),
            "existing_count": len(reconciled.get("existing", [])),
            "new_count": len(reconciled.get("new", [])),
        }

    @staticmethod
    def _select_primary_source_entity(ontology_instance: OntologyInstanceRead) -> str | None:
        """Use one canonical provenance anchor for Scene/Milestone proposals."""
        for entity in ontology_instance.entities:
            if getattr(entity, "entity_instance_id", None):
                return entity.entity_instance_id
        return None

    async def _propose_scenes_milestones(
        self,
        *,
        chunks: list[ChunkInput],
        known_entities: list[ExistingNodeInfo],
        source_entity_instance_id: str | None,
        author_id: str,
    ) -> dict[str, Any]:
        """Generate scene and milestone proposals from the same analysis chunks."""
        logger.info("Step 1B: Proposing scenes and milestones from %d chunks", len(chunks))
        chunk_dump = self._format_chunk_dump(chunks)
        known_aliases = ", ".join(sorted({node.alias for node in known_entities}))
        prompt = ARCHITECT_SCENE_MILESTONE_PROPOSAL_PROMPT.format(
            author_id=author_id,
            source_entity_instance_id=source_entity_instance_id or "",
            known_aliases=known_aliases or "(none)",
            chunk_dump=chunk_dump,
        )

        model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)
        response = await self.llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        parsed = self._parse_scene_milestone_response(response)
        logger.info(
            "architect_v2_scene_parse: scenes=%d",
            len(parsed.scenes),
        )

        now_iso = self._utc_now_iso()
        proposals: list[dict[str, Any]] = []
        milestone_count = 0
        for scene_order, scene in enumerate(parsed.scenes, start=1):
            scene_ref = f"scene-{uuid4()}"
            scene_mentions = self._normalize_mentions(scene.mentions)
            proposals.append(
                {
                    "proposal_type": ArchitectProposalType.PROPOSE_SCENE,
                    "entity_definition_id": None,
                    "entity_instance_id": None,
                    "alias": scene.name,
                    "confidence": 0.8,
                    "justification": scene.description,
                    "proposal_metadata": {
                        "proposal_kind": "scene",
                        "scene_ref": scene_ref,
                        "name": scene.name,
                        "description": scene.description,
                        "created_at": now_iso,
                        "author": {
                            "created_by_type": "agent",
                            "created_by_author": author_id,
                        },
                        "derived_from": {
                            "entity_instance_id": source_entity_instance_id,
                        },
                        "mentions": scene_mentions,
                        "scene_order": scene_order,
                    },
                    "chunks": [],
                }
            )

            milestones = self._coerce_scene_milestones(scene.milestones)
            for milestone_order, milestone in enumerate(milestones, start=1):
                milestone_count += 1
                milestone_ref = f"milestone-{uuid4()}"
                proposals.append(
                    {
                        "proposal_type": ArchitectProposalType.PROPOSE_MILESTONE,
                        "entity_definition_id": None,
                        "entity_instance_id": None,
                        "alias": milestone.label,
                        "confidence": 0.75,
                        "justification": milestone.description,
                        "proposal_metadata": {
                            "proposal_kind": "milestone",
                            "milestone_ref": milestone_ref,
                            "scene_ref": scene_ref,
                            "label": milestone.label,
                            "description": milestone.description,
                            "boundary_type": milestone.boundary_type,
                            "created_at": now_iso,
                            "author": {
                                "created_by_type": "agent",
                                "created_by_author": author_id,
                            },
                            "derived_from": {
                                "entity_instance_id": source_entity_instance_id,
                            },
                            "mentions": self._normalize_mentions(milestone.mentions),
                            "milestone_order": milestone_order,
                        },
                        "chunks": [],
                    }
                )

        return {
            "proposals": proposals,
            "scene_count": len(parsed.scenes),
            "milestone_count": milestone_count,
        }

    async def _propose_relates_to_links(
        self,
        *,
        scene_and_milestone_proposals: list[dict[str, Any]],
        entity_proposals: list[dict[str, Any]],
        known_entities: list[ExistingNodeInfo],
    ) -> list[dict[str, Any]]:
        """
        Resolve mentions from Scene/Milestone proposals against existing graph
        entities and newly proposed entities, then emit RELATES_TO proposals.
        """
        existing_alias_map: dict[str, str] = {}
        for node in known_entities:
            key = self._canonical_alias(node.alias)
            if key and key not in existing_alias_map:
                existing_alias_map[key] = node.node_id

        new_alias_map: dict[str, str] = {}
        for proposal in entity_proposals:
            alias = proposal.get("alias")
            if not alias:
                continue
            canonical = self._canonical_alias(alias)
            if not canonical:
                continue
            if proposal.get("proposal_type") == ArchitectProposalType.NEW_INSTANCE:
                new_alias_map[canonical] = alias

        source_nodes = []
        for proposal in scene_and_milestone_proposals:
            proposal_type = proposal.get("proposal_type")
            if proposal_type not in {
                ArchitectProposalType.PROPOSE_SCENE,
                ArchitectProposalType.PROPOSE_MILESTONE,
            }:
                continue
            metadata = proposal.get("proposal_metadata") or {}
            source_nodes.append(
                {
                    "source_ref": metadata.get("scene_ref")
                    or metadata.get("milestone_ref"),
                    "source_kind": metadata.get("proposal_kind"),
                    "name": proposal.get("alias") or "",
                    "description": metadata.get("description")
                    or proposal.get("justification")
                    or "",
                    "mentions": metadata.get("mentions") or [],
                }
            )

        if not source_nodes:
            logger.info(
                "architect_v2_relates_to: no scene/milestone sources available"
            )
            return []

        logger.info(
            "architect_v2_relates_to: resolving %d sources against %d known entities and %d new entities",
            len(source_nodes),
            len(known_entities),
            len(new_alias_map),
        )

        prompt = ARCHITECT_RELATES_TO_PROPOSAL_PROMPT.format(
            source_nodes=json.dumps(source_nodes, ensure_ascii=False),
            candidate_aliases=json.dumps(
                sorted({node.alias for node in known_entities} | set(new_alias_map.values())),
                ensure_ascii=False,
            ),
        )
        model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)
        response = await self.llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        parsed = self._parse_relates_to_response(response)

        proposals: list[dict[str, Any]] = []
        for item in parsed.relationships:
            canonical = self._canonical_alias(item.target_alias)
            if not canonical:
                continue

            existing_target = existing_alias_map.get(canonical)
            new_target = new_alias_map.get(canonical)

            # Ambiguous links are explicitly dropped by product requirement.
            if existing_target and new_target:
                continue
            if not existing_target and not new_target:
                continue

            target_payload: dict[str, Any]
            if existing_target:
                target_payload = {
                    "kind": "existing_entity",
                    "entity_instance_id": existing_target,
                    "alias": item.target_alias,
                }
            else:
                target_payload = {
                    "kind": "new_entity_proposal",
                    "alias": new_target,
                }

            proposals.append(
                {
                    "proposal_type": ArchitectProposalType.PROPOSE_RELATES_TO,
                    "entity_definition_id": None,
                    "entity_instance_id": None,
                    "alias": f"{item.source_kind}:{item.source_ref} -> {item.target_alias}",
                    "confidence": item.confidence,
                    "justification": item.evidence,
                    "proposal_metadata": {
                        "proposal_kind": "relates_to",
                        "source_ref": item.source_ref,
                        "source_kind": item.source_kind,
                        "target": target_payload,
                        "relationship": "RELATES_TO",
                        "confidence": item.confidence,
                        "evidence": item.evidence,
                    },
                    "chunks": [],
                }
            )

        return proposals

    async def _load_node_catalogue(
        self, ontology_ids: list[int]
    ) -> list[ExistingNodeInfo]:
        """
        Load existing nodes from the knowledge graph.

        Returns a list of ExistingNodeInfo objects.
        """
        # Use the graph retriever to get all nodes for the given ontologies
        # For now, we'll do a broad search to get existing nodes
        # In production, this could be optimized with a dedicated query
        if not ontology_ids:
            return []

        nodes: list[ExistingNodeInfo] = []
        seen_ids: set[str] = set()
        batch_size = 500

        fetch_method = getattr(self.graph_retriever, "list_entities_by_ontology", None)

        def _add_nodes_from_batch(batch: list[dict[str, Any] | ExistingNodeInfo]):
            for entry in batch:
                if isinstance(entry, ExistingNodeInfo):
                    node_id = entry.node_id
                    alias = entry.alias
                    ontology = entry.ontology
                else:
                    node_id = entry.get("node_id")  # type: ignore[arg-type]
                    alias = entry.get("alias")  # type: ignore[arg-type]
                    ontology = entry.get("ontology")  # type: ignore[arg-type]

                if not node_id or not alias or node_id in seen_ids:
                    continue

                nodes.append(
                    ExistingNodeInfo(
                        node_id=node_id,
                        alias=alias,
                        ontology=ontology or "Unknown",
                    )
                )
                seen_ids.add(node_id)

        if callable(fetch_method):
            for ontology_id in ontology_ids:
                skip = 0
                while True:
                    try:
                        batch = await fetch_method(  # type: ignore[misc]
                            ontology_id=ontology_id,
                            skip=skip,
                            limit=batch_size,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to load node catalogue batch: ontology=%s skip=%s error=%s",
                            ontology_id,
                            skip,
                            exc,
                        )
                        break

                    if not batch:
                        break

                    _add_nodes_from_batch(batch)

                    if len(batch) < batch_size:
                        break
                    skip += batch_size
        else:
            # Fallback to semantic search when list_entities_by_ontology is unavailable
            try:
                results = await self.graph_retriever.search_aliases(
                    query="",
                    ontology_ids=ontology_ids,
                    top_k=batch_size,
                )
                _add_nodes_from_batch(
                    [
                        {
                            "node_id": result.node_id,
                            "alias": result.node_alias,
                            "ontology": result.source,
                        }
                        for result in results
                    ]
                )
            except Exception as exc:
                logger.warning("Failed to load node catalogue: %s", exc)

        logger.info("Loaded %d existing nodes", len(nodes))
        return nodes

    async def _extract_chunk_entities(
        self, chunks: list[ChunkInput], ontology_definitions: str
    ) -> list[dict[str, Any]]:
        """
        Step 1: Extract entities from each chunk using LLM.

        Returns a list of chunk results with entity proposals.
        """
        semaphore = asyncio.Semaphore(self.chunk_concurrency)
        extract_model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)

        async def process_chunk(chunk: ChunkInput) -> dict[str, Any]:
            try:
                async with semaphore:
                    prompt = ARCHITECT_CHUNK_EXTRACTION_PROMPT.format(
                        ontology_definitions=ontology_definitions,
                        chunk_text=chunk.text.strip(),
                    )

                    response_text = await self.llm_client.chat(
                        model=extract_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                    )

                    # Parse the response
                    parsed = self._parse_chunk_extraction(response_text, chunk.index)

                    return {
                        "chunk_id": f"chunk_{chunk.index:03d}",
                        "chunk_index": chunk.index,
                        "entities": parsed.entities,
                    }
            except Exception as exc:
                logger.error(
                    "architect_v2_chunk_error: chunk=%d error=%s",
                    chunk.index,
                    exc,
                    exc_info=True,
                )
                return {
                    "chunk_id": f"chunk_{chunk.index:03d}",
                    "chunk_index": chunk.index,
                    "entities": [],
                }

        chunk_results = await asyncio.gather(
            *(process_chunk(chunk) for chunk in chunks)
        )

        return chunk_results

    def _deduplicate_entities(
        self, chunk_results: list[dict[str, Any]]
    ) -> list[DedupedEntityProposal]:
        """
        Step 2: Deduplicate entities across all chunks programmatically.

        Uses canonical name matching to identify duplicates.
        """
        # Collect all entities with their sources
        entity_map: dict[str, dict[str, Any]] = {}

        for result in chunk_results:
            for entity in result.get("entities", []):
                name = entity.name
                canonical_name = self._canonical_alias(name)

                if not canonical_name:
                    continue

                existing_key = self._find_matching_canonical_key(
                    canonical_name, entity_map
                )

                # Use canonical name as key
                key = existing_key or canonical_name
                if key not in entity_map:
                    entity_map[key] = {
                        "name": name,  # Keep the first/longest variant
                        "ontology": entity.ontology,
                        "confidences": [],
                        "justifications": [],
                        "chunk_indices": [],
                        "name_variants": set(),
                    }

                entry = entity_map[key]
                entry["confidences"].append(entity.confidence)
                entry["justifications"].append(entity.why)
                entry["chunk_indices"].append(result["chunk_index"])
                entry["name_variants"].add(name)

                # Keep the longest/most complete name variant
                if len(name) > len(entry["name"]):
                    entry["name"] = name

        # Create deduped proposals
        deduped = []
        for canonical_name, data in entity_map.items():
            avg_confidence = sum(data["confidences"]) / len(data["confidences"])
            deduped.append(
                DedupedEntityProposal(
                    name=data["name"],
                    ontology=data["ontology"],
                    confidence=avg_confidence,
                    justifications=list(dict.fromkeys(data["justifications"])),
                    chunk_indices=sorted(set(data["chunk_indices"])),
                )
            )

        return deduped

    def _find_matching_canonical_key(
        self, canonical_name: str, entity_map: dict[str, dict[str, Any]]
    ) -> str | None:
        """Return existing canonical key if the alias closely matches."""
        if canonical_name in entity_map:
            return canonical_name
        for existing_key in entity_map.keys():
            if self._aliases_equivalent(existing_key, canonical_name):
                return existing_key
        return None

    @staticmethod
    def _aliases_equivalent(alias_a: str | None, alias_b: str | None) -> bool:
        """Soft matching used to collapse aliases like 'Jessie' and 'Jessie Williams'."""
        if not alias_a or not alias_b:
            return False
        if alias_a == alias_b:
            return True

        tokens_a = alias_a.split()
        tokens_b = alias_b.split()
        if not tokens_a or not tokens_b:
            return False

        # Helper closures keep comparisons symmetric
        def _one_token_matches_first_or_last(
            single_tokens: list[str], multi_tokens: list[str]
        ) -> bool:
            token = single_tokens[0]
            if token == multi_tokens[-1]:
                return True
            if len(multi_tokens) <= 2 and token == multi_tokens[0]:
                return True
            return False

        if len(tokens_a) == 1 and len(tokens_b) > 1:
            return _one_token_matches_first_or_last(tokens_a, tokens_b)
        if len(tokens_b) == 1 and len(tokens_a) > 1:
            return _one_token_matches_first_or_last(tokens_b, tokens_a)
        return False

    def _prefilter_node_catalogue(
        self,
        deduped_entities: list[DedupedEntityProposal],
        node_catalogue: list[ExistingNodeInfo],
    ) -> tuple[list[ExistingNodeInfo], dict[str, ExistingNodeInfo]]:
        """
        Pre-filter the node catalogue to only include candidates likely to match.

        Uses programmatic name matching to reduce the catalogue size before
        sending to the LLM. This prevents performance issues when the catalogue
        contains thousands of nodes.

        Returns:
            A tuple of (filtered_catalogue, exact_matches) where:
            - filtered_catalogue: nodes that might match any proposed entity
            - exact_matches: dict mapping canonical name -> node for exact matches
        """
        if not deduped_entities or not node_catalogue:
            return [], {}

        # Build a lookup of canonical names for existing nodes
        existing_by_canonical: dict[str, list[ExistingNodeInfo]] = defaultdict(list)
        for node in node_catalogue:
            canonical = self._canonical_alias(node.alias)
            if canonical:
                existing_by_canonical[canonical].append(node)

        # For each proposed entity, find potentially matching existing nodes
        filtered_nodes: dict[str, ExistingNodeInfo] = {}  # node_id -> node
        exact_matches: dict[str, ExistingNodeInfo] = {}  # canonical_name -> node
        # Track matches per entity for detailed logging
        entity_matches: dict[str, list[str]] = (
            {}
        )  # entity name -> list of matched aliases

        for entity in deduped_entities:
            canonical_proposed = self._canonical_alias(entity.name)
            if not canonical_proposed:
                continue

            matched_nodes_for_entity: list[str] = []

            # Check for exact canonical match
            if canonical_proposed in existing_by_canonical:
                for node in existing_by_canonical[canonical_proposed]:
                    filtered_nodes[node.node_id] = node
                    matched_nodes_for_entity.append(node.alias)
                    # Record the first exact match for this proposed entity
                    if canonical_proposed not in exact_matches:
                        exact_matches[canonical_proposed] = node
                entity_matches[entity.name] = matched_nodes_for_entity
                continue

            # Check for partial/fuzzy matches using token-based similarity
            proposed_tokens = set(canonical_proposed.split())
            for existing_canonical, nodes in existing_by_canonical.items():
                existing_tokens = set(existing_canonical.split())

                # Check if aliases are equivalent (handles "Jessie" vs "Jessie Williams")
                if self._aliases_equivalent(canonical_proposed, existing_canonical):
                    for node in nodes:
                        filtered_nodes[node.node_id] = node
                        matched_nodes_for_entity.append(node.alias)
                    continue

                # Check for significant token overlap (at least one shared token,
                # and at least 50% of the smaller set's tokens match)
                shared_tokens = proposed_tokens & existing_tokens
                if shared_tokens:
                    min_tokens = min(len(proposed_tokens), len(existing_tokens))
                    overlap_ratio = len(shared_tokens) / min_tokens
                    if overlap_ratio >= MIN_TOKEN_OVERLAP_RATIO:
                        for node in nodes:
                            filtered_nodes[node.node_id] = node
                            matched_nodes_for_entity.append(node.alias)

            entity_matches[entity.name] = matched_nodes_for_entity

        logger.info(
            "architect_v2_prefilter: proposed=%d catalogue=%d filtered=%d exact=%d",
            len(deduped_entities),
            len(node_catalogue),
            len(filtered_nodes),
            len(exact_matches),
        )

        # Log detailed per-entity matches for debugging
        for entity_name, matches in entity_matches.items():
            if matches:
                logger.info(
                    "architect_v2_prefilter_entity_match: proposed='%s' -> candidates=%s",
                    entity_name,
                    matches[:5],  # Limit to 5 to avoid excessive logging
                )
            else:
                logger.info(
                    "architect_v2_prefilter_entity_match: proposed='%s' -> no candidates found",
                    entity_name,
                )

        return list(filtered_nodes.values()), exact_matches

    async def _reconcile_with_existing(
        self,
        deduped_entities: list[DedupedEntityProposal],
        node_catalogue: list[ExistingNodeInfo],
        ontology_definitions: str,
    ) -> dict[str, Any]:
        """
        Step 3: Use LLM to reconcile proposed entities with existing ones.

        First applies programmatic pre-filtering to reduce the catalogue size,
        then uses LLM for final reconciliation on ambiguous cases.

        Returns a dictionary with 'existing' and 'new' lists.
        """
        if not deduped_entities:
            return {"existing": [], "new": []}

        # Pre-filter the catalogue to only include likely matches
        filtered_catalogue, exact_matches = self._prefilter_node_catalogue(
            deduped_entities, node_catalogue
        )

        # Build result lists - start with programmatic exact matches
        existing_results: list[dict[str, Any]] = []
        entities_needing_llm: list[DedupedEntityProposal] = []

        for entity in deduped_entities:
            canonical = self._canonical_alias(entity.name)
            if canonical in exact_matches:
                # Exact match found - no need for LLM
                matched_node = exact_matches[canonical]
                existing_results.append(
                    {
                        "proposed_name": entity.name,
                        "matched_node_id": matched_node.node_id,
                        "ontology": entity.ontology,
                    }
                )
            else:
                # Need LLM to decide
                entities_needing_llm.append(entity)

        # If all entities were matched exactly, return early
        if not entities_needing_llm:
            logger.info(
                "architect_v2_reconcile: all %d entities matched exactly, skipping LLM",
                len(deduped_entities),
            )
            return {
                "existing": existing_results,
                "new": [],
            }

        # If there's no filtered catalogue, all remaining entities are new
        if not filtered_catalogue:
            logger.info(
                "architect_v2_reconcile: no candidates for %d entities, marking as new",
                len(entities_needing_llm),
            )
            return {
                "existing": existing_results,
                "new": [
                    {"name": e.name, "ontology": e.ontology}
                    for e in entities_needing_llm
                ],
            }

        # Format entities for LLM - only those needing reconciliation
        proposed_list = [
            {"name": e.name, "ontology": e.ontology} for e in entities_needing_llm
        ]

        # Format filtered existing entities for LLM
        existing_list = [
            {"node_id": n.node_id, "alias": n.alias, "ontology": n.ontology}
            for n in filtered_catalogue
        ]

        logger.info(
            "architect_v2_reconcile_llm: sending %d proposed, %d candidates to LLM",
            len(proposed_list),
            len(existing_list),
        )

        # Prepare prompt
        prompt = ARCHITECT_RECONCILIATION_PROMPT.format(
            ontology_definitions=ontology_definitions,
            proposed_entities=json.dumps(proposed_list, indent=2),
            existing_entities=json.dumps(existing_list, indent=2),
        )

        # Call LLM
        extract_model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)
        try:
            response_text = await self.llm_client.chat(
                model=extract_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            # Parse response
            parsed = self._parse_reconciliation(response_text)

            # Only keep matches that point to ids we actually know about.
            valid_ids = {n.node_id for n in filtered_catalogue}
            for entry in parsed.existing:
                if entry.matched_node_id in valid_ids:
                    existing_results.append(entry.model_dump())
                else:
                    logger.warning(
                        "architect_v2_invalid_match_id: proposed=%s matched_id=%s",
                        entry.proposed_name,
                        entry.matched_node_id,
                    )

            return {
                "existing": existing_results,
                "new": [n.model_dump() for n in parsed.new],
            }
        except Exception as exc:
            logger.error("Reconciliation failed: %s", exc, exc_info=True)
            # Fallback: treat remaining as new, keep exact matches
            return {
                "existing": existing_results,
                "new": [
                    {"name": e.name, "ontology": e.ontology}
                    for e in entities_needing_llm
                ],
            }

    def _create_final_proposals(
        self,
        chunk_results: list[dict[str, Any]],
        deduped_entities: list[DedupedEntityProposal],
        reconciled: dict[str, Any],
        *,
        allowed_ontology_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Step 4: Create final proposals with resolved status.

        Maps reconciliation results back to the original entities with metadata.
        """
        # Create lookup maps
        existing_map = {}
        for entry in reconciled.get("existing", []):
            proposed = entry.get("proposed_name")
            matched_id = entry.get("matched_node_id")
            key = self._canonical_alias(proposed)
            if not key or not matched_id:
                # Without a valid match id we cannot treat it as an update
                continue
            existing_map[key] = entry

        new_set = {
            self._canonical_alias(n["name"])
            for n in reconciled.get("new", [])
            if self._canonical_alias(n.get("name"))
        }

        # Create entity lookup by canonical name
        entity_lookup = {self._canonical_alias(e.name): e for e in deduped_entities}

        proposals = []

        for entity in deduped_entities:
            canonical_name = self._canonical_alias(entity.name)

            # Combine justifications
            combined_justification = " | ".join(entity.justifications[:3])

            # Prepare metadata
            metadata = {
                "resolved_status": None,
                "mention_count": len(entity.chunk_indices),
                "chunk_indices": entity.chunk_indices,
                "ontology_name": entity.ontology,
            }

            # Check if it's existing or new
            if canonical_name in existing_map:
                if not self._is_allowed_update(entity.ontology, allowed_ontology_names):
                    logger.info(
                        "architect_v2_skip_update: alias=%s ontology=%s",
                        entity.name,
                        entity.ontology,
                    )
                    continue

                matched = existing_map[canonical_name]
                matched_id = matched.get("matched_node_id")
                # print (f"[ARCHITECT] Matched ID for {canonical_name}: {entity.name}({matched_id})")

                # Only treat as existing if there is a valid matched node id.

                if not matched_id:
                    metadata["resolved_status"] = "new"
                    proposals.append(
                        {
                            "proposal_type": ArchitectProposalType.NEW_INSTANCE,
                            "entity_definition_id": None,
                            "entity_instance_id": None,
                            "alias": entity.name,
                            "confidence": entity.confidence,
                            "justification": combined_justification,
                            "proposal_metadata": metadata,
                            "chunks": [],
                        }
                    )
                    continue
                metadata["resolved_status"] = "existing"

                proposals.append(
                    {
                        "proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
                        "entity_definition_id": None,  # Will be filled by ontology name lookup
                        "entity_instance_id": matched_id,
                        "alias": entity.name,
                        "confidence": entity.confidence,
                        "justification": combined_justification,
                        "proposal_metadata": metadata,
                        "chunks": [],  # Can be populated from chunk_results if needed
                    }
                )
            elif canonical_name in new_set:
                metadata["resolved_status"] = "new"
                proposals.append(
                    {
                        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
                        "entity_definition_id": None,  # Will be filled by ontology name lookup
                        "entity_instance_id": None,
                        "alias": entity.name,
                        "confidence": entity.confidence,
                        "justification": combined_justification,
                        "proposal_metadata": metadata,
                        "chunks": [],  # Can be populated from chunk_results if needed
                    }
                )
            else:
                # Default to new when reconciliation could not classify the entity.
                metadata["resolved_status"] = "new"
                proposals.append(
                    {
                        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
                        "entity_definition_id": None,
                        "entity_instance_id": None,
                        "alias": entity.name,
                        "confidence": entity.confidence,
                        "justification": combined_justification,
                        "proposal_metadata": metadata,
                        "chunks": [],
                    }
                )

        return proposals

    def _build_chunks(
        self,
        ontology_instance: OntologyInstanceRead,
        *,
        chunk_size: int,
        max_chunks: int | None,
    ) -> list[ChunkInput]:
        """Build text chunks from the ontology instance."""
        chunks: list[ChunkInput] = []
        chunk_index = 0

        for entity in ontology_instance.entities:
            text_parts = []
            if entity.text:
                text_parts.append(self._strip_html(entity.text))
            if entity.autogenerated_text:
                text_parts.append(self._strip_html(entity.autogenerated_text))

            if not text_parts:
                continue

            combined = "\n\n".join(part.strip() for part in text_parts if part.strip())
            if not combined:
                continue

            for chunk_text in self._chunk_text(
                combined, chunk_size, self.chunk_overlap
            ):
                chunks.append(
                    ChunkInput(
                        index=chunk_index,
                        entity_alias=entity.alias,
                        entity_definition_id=entity.definition_id,
                        text=chunk_text,
                    )
                )
                chunk_index += 1

        if max_chunks is not None and len(chunks) > max_chunks:
            return chunks[:max_chunks]
        return chunks

    @staticmethod
    def _chunk_text(text: str, chunk_size: int, overlap: int) -> Iterable[str]:
        """Split text into chunks based on word count."""
        words = text.split()
        total_words = len(words)

        if total_words <= chunk_size:
            yield text
            return

        start = 0
        while start < total_words:
            end = min(start + chunk_size, total_words)
            chunk_words = words[start:end]
            yield " ".join(chunk_words)

            if end >= total_words:
                break

            start = max(0, end - overlap)

    @staticmethod
    def _format_ontology_definitions(
        entity_definitions: Iterable[dict[str, Any]],
    ) -> str:
        """Format ontology definitions for LLM prompt."""
        lines = []
        for definition in entity_definitions:
            desc = (definition.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 240:
                desc = desc[:240] + "…"
            lines.append(f"- {definition['name']}: {desc or 'No description'}")
        return "\n".join(lines) if lines else "(no ontology definitions)"

    @staticmethod
    def _build_allowed_ontology_names(
        entity_definitions: Iterable[dict[str, Any]],
    ) -> set[str]:
        """Collect ontology names that are permitted for automated updates."""
        allowed: set[str] = set()
        for definition in entity_definitions:
            name = (definition.get("name") or "").strip().lower()
            if name:
                allowed.add(name)
        return allowed

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_mentions(mentions: list[str] | None) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for mention in mentions or []:
            cleaned = (mention or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(cleaned)
        return unique

    def _coerce_scene_milestones(self, milestones: list[Any]) -> list[Any]:
        """
        Ensure each scene has exactly one begin and one end milestone.
        If missing, synthetic boundary milestones are added.
        """
        if not milestones:
            return [
                type("Milestone", (), {
                    "label": "Scene begins",
                    "description": "The scene begins",
                    "boundary_type": "begin",
                    "mentions": [],
                })(),
                type("Milestone", (), {
                    "label": "Scene ends",
                    "description": "The scene ends",
                    "boundary_type": "end",
                    "mentions": [],
                })(),
            ]

        normalized = list(milestones)
        begin_indices = [
            idx
            for idx, item in enumerate(normalized)
            if str(getattr(item, "boundary_type", "none")).lower() == "begin"
        ]
        end_indices = [
            idx
            for idx, item in enumerate(normalized)
            if str(getattr(item, "boundary_type", "none")).lower() == "end"
        ]

        if not begin_indices:
            normalized.insert(
                0,
                type("Milestone", (), {
                    "label": "Scene begins",
                    "description": "The scene begins",
                    "boundary_type": "begin",
                    "mentions": [],
                })(),
            )
        elif len(begin_indices) > 1:
            for idx in begin_indices[1:]:
                setattr(normalized[idx], "boundary_type", "none")

        if not end_indices:
            normalized.append(
                type("Milestone", (), {
                    "label": "Scene ends",
                    "description": "The scene ends",
                    "boundary_type": "end",
                    "mentions": [],
                })(),
            )
        elif len(end_indices) > 1:
            for idx in end_indices[:-1]:
                setattr(normalized[idx], "boundary_type", "none")

        return normalized

    def _format_chunk_dump(self, chunks: list[ChunkInput], max_chars: int = 16000) -> str:
        lines: list[str] = []
        consumed = 0
        for chunk in chunks:
            block = (
                f"[chunk {chunk.index}] source_alias={chunk.entity_alias or ''}\n"
                f"{chunk.text.strip()}\n"
            )
            if consumed + len(block) > max_chars:
                break
            lines.append(block)
            consumed += len(block)
        return "\n".join(lines)

    def _parse_scene_milestone_response(
        self, response_text: str
    ) -> SceneMilestoneProposalResponse:
        try:
            json_block = self._extract_json_block(response_text)
            payload = json.loads(json_block)
            return SceneMilestoneProposalResponse.model_validate(payload)
        except Exception as exc:
            logger.warning("architect_v2_scene_parse_error: %s", exc)
            return SceneMilestoneProposalResponse(scenes=[])

    def _parse_relates_to_response(self, response_text: str) -> RelatesToProposalResponse:
        try:
            json_block = self._extract_json_block(response_text)
            payload = json.loads(json_block)
            return RelatesToProposalResponse.model_validate(payload)
        except Exception as exc:
            logger.warning("architect_v2_relates_to_parse_error: %s", exc)
            return RelatesToProposalResponse(relationships=[])

    @staticmethod
    def _extract_json_block(raw: str) -> str:
        """Extract JSON object from LLM response."""
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in LLM response")
        return raw[start : end + 1]

    def _parse_chunk_extraction(
        self, response_text: str, chunk_index: int
    ) -> ChunkExtractionResponse:
        """Parse the LLM response for chunk-level entity extraction."""
        try:
            json_block = self._extract_json_block(response_text)
            payload = json.loads(json_block)
            parsed = ChunkExtractionResponse.model_validate(payload)
            return parsed
        except Exception as exc:
            logger.warning(
                "architect_v2_parse_error: chunk=%d error=%s",
                chunk_index,
                exc,
            )
            return ChunkExtractionResponse(entities=[])

    def _parse_reconciliation(self, response_text: str) -> ReconciliationResponse:
        """Parse the LLM response for reconciliation."""
        try:
            json_block = self._extract_json_block(response_text)
            payload = json.loads(json_block)
            parsed = ReconciliationResponse.model_validate(payload)

            # Filter out entries with null matched_node_id (LLM sometimes returns
            # null for existing entries it couldn't match) and treat them as new
            valid_existing = []
            invalid_existing = []
            for entry in parsed.existing:
                if entry.matched_node_id:
                    valid_existing.append(entry)
                else:
                    invalid_existing.append(entry)

            if invalid_existing:
                logger.warning(
                    "architect_v2_reconciliation_filtered: %d entries with null "
                    "matched_node_id moved to new: %s",
                    len(invalid_existing),
                    [e.proposed_name for e in invalid_existing],
                )
                # Add invalid entries to new list (they couldn't be matched)
                parsed.new.extend(
                    ReconciledNewEntity(name=e.proposed_name, ontology=e.ontology)
                    for e in invalid_existing
                )

            parsed.existing = valid_existing
            return parsed
        except Exception as exc:
            logger.warning("architect_v2_reconciliation_parse_error: %s", exc)
            return ReconciliationResponse(existing=[], new=[])

    @staticmethod
    def _canonical_alias(alias: str | None) -> str:
        """
        Convert an alias to its canonical form for deduplication.

        Rules:
        - Remove parenthetical content: "Mithras (god)" -> "mithras"
        - Remove titles: "Dr. Smith" -> "smith"
        - Lowercase and normalize whitespace
        """
        if not alias:
            return ""

        value = alias.strip().lower()
        value = value.strip("\"'")

        # Remove parenthetical content
        value = re.sub(r"\([^()]*\)", " ", value)

        # Handle colons (keep the last part)
        if ":" in value:
            parts = [part.strip() for part in value.split(":") if part.strip()]
            if parts:
                value = parts[-1]

        # Handle commas (keep the first part)
        if "," in value:
            value = value.split(",", 1)[0].strip()

        # Remove common titles
        title_prefixes = {
            "professor",
            "prof",
            "doctor",
            "dr",
            "mr",
            "mrs",
            "miss",
            "ms",
            "sir",
            "lady",
            "lord",
            "captain",
            "capt",
            "major",
            "colonel",
            "father",
            "mother",
            "queen",
            "king",
            "prince",
            "princess",
        }
        words = value.split()
        while words and words[0].rstrip(".") in title_prefixes:
            words.pop(0)

        # Remove leading articles
        leading_articles = {"the", "a", "an"}
        if words and words[0] in leading_articles:
            words.pop(0)

        value = " ".join(words)

        # Remove special characters except hyphens
        value = re.sub(r"[^a-z0-9\s-]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()

        return value

    def _strip_html(self, text: str | None) -> str:
        """Strip HTML tags from text."""
        if not text:
            return ""

        class _HTMLStripper(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag in {"br", "p", "div", "li"}:
                    self.parts.append("\n")

            def handle_endtag(self, tag: str) -> None:
                if tag in {"p", "div", "li"}:
                    self.parts.append("\n")

            def handle_data(self, data: str) -> None:
                self.parts.append(data)

            def get_text(self) -> str:
                return "".join(self.parts)

        stripper = _HTMLStripper()
        stripper.feed(text)
        stripped = stripper.get_text()
        return " ".join(unescape(stripped).split())

    @staticmethod
    def _is_allowed_update(
        ontology_name: str | None, allowed_names: set[str] | None
    ) -> bool:
        """Return True when updates are allowed for the provided ontology."""
        if not allowed_names:
            return True
        normalized = (ontology_name or "").strip().lower()
        return normalized in allowed_names
