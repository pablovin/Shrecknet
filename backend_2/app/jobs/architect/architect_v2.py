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
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.architect.prompts import (
    ARCHITECT_CHUNK_EXTRACTION_PROMPT,
    ARCHITECT_RECONCILIATION_PROMPT,
)
from app.jobs.architect.schemas import (
    ChunkExtractionResponse,
    DedupedEntityProposal,
    ExistingNodeInfo,
    FinalEntityProposal,
    ReconciliationResponse,
)
from app.models.architect import ArchitectProposalType
from app.schemas.ontology_instance import OntologyInstanceRead

logger = logging.getLogger(__name__)


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

        # Step 1: Chunk-level entity extraction
        logger.info("Step 1: Extracting entities from %d chunks", len(chunks))
        chunk_results = await self._extract_chunk_entities(chunks, ontology_definitions)

        # Step 2: Global deduplication
        logger.info("Step 2: Deduplicating entities across chunks")
        deduped_entities = self._deduplicate_entities(chunk_results)
        logger.info("Deduplication: %d unique entities found", len(deduped_entities))

        # Step 3: Reconciliation with existing entities
        logger.info("Step 3: Reconciling with existing entities")
        # logger.info(f"[ARCHITECT] Node Catalogue: {node_catalogue}")
        reconciled = await self._reconcile_with_existing(
            deduped_entities, node_catalogue, ontology_definitions
        )

        # Step 4: Map back to final JSON
        logger.info("Step 4: Creating final proposals")
        proposals = self._create_final_proposals(
            chunk_results, deduped_entities, reconciled
        )
        # logger.info(f"[ARCHITECT] Proposals: {proposals}")

        return {
            "proposals": proposals,
            "chunks": chunk_results,
            "chunk_count": len(chunks),
            "pipeline_version": "v2",
            "deduped_count": len(deduped_entities),
            "existing_count": len(reconciled.get("existing", [])),
            "new_count": len(reconciled.get("new", [])),
        }

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
        nodes = []

        # Search with empty query to get all nodes (or top nodes)
        try:
            results = await self.graph_retriever.search_aliases(
                query="",
                ontology_ids=ontology_ids,
                top_k=500,  # Get a large number of existing nodes
            )

            for result in results:
                if result.node_id and result.node_alias:
                    nodes.append(
                        ExistingNodeInfo(
                            node_id=result.node_id,
                            alias=result.node_alias,
                            ontology=result.source or "Unknown",
                        )
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

                # Use canonical name as key
                if canonical_name not in entity_map:
                    entity_map[canonical_name] = {
                        "name": name,  # Keep the first/longest variant
                        "ontology": entity.ontology,
                        "confidences": [],
                        "justifications": [],
                        "chunk_indices": [],
                        "name_variants": set(),
                    }

                entry = entity_map[canonical_name]
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

    async def _reconcile_with_existing(
        self,
        deduped_entities: list[DedupedEntityProposal],
        node_catalogue: list[ExistingNodeInfo],
        ontology_definitions: str,
    ) -> dict[str, Any]:
        """
        Step 3: Use LLM to reconcile proposed entities with existing ones.

        Returns a dictionary with 'existing' and 'new' lists.
        """
        if not deduped_entities:
            return {"existing": [], "new": []}

        # Format proposed entities for LLM
        proposed_list = [
            {"name": e.name, "ontology": e.ontology} for e in deduped_entities
        ]

        # Format existing entities for LLM
        existing_list = [
            {"node_id": n.node_id, "alias": n.alias, "ontology": n.ontology}
            for n in node_catalogue
        ]

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
            valid_ids = {n.node_id for n in node_catalogue}
            filtered_existing = []
            for entry in parsed.existing:
                if entry.matched_node_id in valid_ids:
                    filtered_existing.append(entry)
                else:
                    logger.warning(
                        "architect_v2_invalid_match_id: proposed=%s matched_id=%s",
                        entry.proposed_name,
                        entry.matched_node_id,
                    )

            return {
                "existing": [e.model_dump() for e in filtered_existing],
                "new": [n.model_dump() for n in parsed.new],
            }
        except Exception as exc:
            logger.error("Reconciliation failed: %s", exc, exc_info=True)
            # Fallback: treat all as new
            return {
                "existing": [],
                "new": [
                    {"name": e.name, "ontology": e.ontology} for e in deduped_entities
                ],
            }

    def _create_final_proposals(
        self,
        chunk_results: list[dict[str, Any]],
        deduped_entities: list[DedupedEntityProposal],
        reconciled: dict[str, Any],
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
