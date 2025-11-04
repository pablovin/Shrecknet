from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.architect.prompts import ARCHITECT_EXTRACTION_PROMPT
from app.jobs.architect.schemas import (
    ArchitectLLMResponse,
    ChunkAnalysisResult,
    ExtractedExistingInstance,
    ExtractedNewInstance,
)
from app.jobs.elder.schemas import RetrievedChunk
from app.models.architect import ArchitectProposalType
from app.schemas.ontology_instance import OntologyInstanceRead
import datetime

logger = logging.getLogger(__name__)


@dataclass
class ChunkInput:
    """Simple container representing a chunk of text to analyse."""

    index: int
    entity_alias: str | None
    entity_definition_id: int | None
    text: str


class ArchitectOrchestrator:
    """Coordinates the Architect step-one extraction pipeline."""

    def __init__(
        self,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
        retrieval_top_k: int = 20,
        chunk_concurrency: int = 4,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.retrieval_top_k = retrieval_top_k
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
        chunks = self._build_chunks(
            ontology_instance,
            chunk_size=override_chunk_size or self.chunk_size,
            max_chunks=override_max_chunks,
        )
        logger.info(
            "architect_chunks: instance=%s chunk_count=%d",
            ontology_instance.instance_id,
            len(chunks),
        )
        if not chunks:
            return {"proposals": [], "chunks": [], "chunk_count": 0}

        entity_catalog = self._format_entity_catalog(entity_definitions)
        semaphore = asyncio.Semaphore(self.chunk_concurrency)
        extract_model = self.model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)

        async def process_chunk(chunk: ChunkInput) -> ChunkAnalysisResult:
            try:
                async with semaphore:
                    now = datetime.datetime.now()

                    retrieval = await self.graph_retriever.search_aliases(
                        query=chunk.text,
                        ontology_ids=agent_ontology_ids,
                        top_k=self.retrieval_top_k,
                    )

                    retrieval_time = (datetime.datetime.now() - now).total_seconds()
                    # Build a map of entity_instance_id to alias from retrieval results
                    retrieval_alias_map = {}
                    for retrieved_chunk in retrieval:
                        if retrieved_chunk.node_id and retrieved_chunk.node_label:
                            retrieval_alias_map[retrieved_chunk.node_id] = (
                                retrieved_chunk.node_label
                            )

                    now = datetime.datetime.now()
                    prompt = ARCHITECT_EXTRACTION_PROMPT.format(
                        entity_catalog=entity_catalog,
                        existing_instances=self._format_retrieval_summary(retrieval),
                        chunk_text=chunk.text.strip(),
                    )

                    prompt_time = (datetime.datetime.now() - now).total_seconds()

                    # print (f"[LOGGING] Retrieval alias map: {retrieval_alias_map}")

                    logger.debug(
                        "architect_prompt_chunk=%d size=%d", chunk.index, len(prompt)
                    )
                    now = datetime.datetime.now()
                    response_text = await self.llm_client.chat(
                        model=extract_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                    )
                    response_time = (datetime.datetime.now() - now).total_seconds()

                    print(
                        f"[LOGGING] entity_catalog: {entity_catalog} \n"
                        + f"[LOGGING] retrieval_alias_map: {retrieval_alias_map} \n"
                        + f"[LOGGING] prompt: {prompt} \n"
                        + f"[LOGGING] response_text: {response_text} \n"
                        + f"[LOGGING] retrieval_time: {retrieval_time} seconds \n"
                        + f"[LOGGING] prompt_time: {prompt_time} seconds\n"
                        + f"[LOGGING] response_time: {response_time} seconds\n"
                    )

            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "architect_chunk_error: chunk=%d error=%s",
                    chunk.index,
                    exc,
                    exc_info=True,
                )
                response_text = (
                    '{\n  "new_instances": [],\n  "existing_instances": []\n}'
                )
                retrieval_alias_map = {}
            parsed = self._parse_llm_response(
                response_text,
                chunk_index=chunk.index,
                chunk_text=chunk.text,
                source_alias=chunk.entity_alias,
                source_definition_id=chunk.entity_definition_id,
                retrieval_alias_map=retrieval_alias_map,
            )

            print(f"[[LOGGING] Parsed response for chunk {chunk.index}: {parsed}")
            return parsed

        chunk_results = await asyncio.gather(
            *(process_chunk(chunk) for chunk in chunks)
        )

        proposals = self._aggregate_results(chunk_results)
        proposals = self._postprocess_proposals(proposals)
        return {
            "proposals": proposals,
            "chunks": chunk_results,
            "chunk_count": len(chunks),
        }

    def _build_chunks(
        self,
        ontology_instance: OntologyInstanceRead,
        *,
        chunk_size: int,
        max_chunks: int | None,
    ) -> list[ChunkInput]:
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
        """
        Split text into chunks based on word count.

        Args:
            text: The text to chunk
            chunk_size: Number of words per chunk
            overlap: Number of words to overlap between chunks

        Returns:
            Iterator of text chunks
        """
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

            # Move start forward, accounting for overlap
            start = max(0, end - overlap)

    @staticmethod
    def _format_entity_catalog(entity_definitions: Iterable[dict[str, Any]]) -> str:
        lines = []
        for definition in entity_definitions:
            desc = (definition.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 240:
                desc = desc[:240] + "…"
            lines.append(
                f"- {definition['id']}: {definition['name']} — {desc or 'No description'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_retrieval_summary(retrieval: Iterable[RetrievedChunk]) -> str:
        lines = []
        for chunk in retrieval:
            # preview = (chunk.text or "").strip().replace("\n", " ")
            # if len(preview) > 200:
            #     preview = preview[:200] + "…"

            lines.append(
                f"- Entity Id = {chunk.node_id} | Entity alias={chunk.node_alias or '?'} | Retrieval Score={chunk.score:.3f} "
            )
        return "\n".join(lines) if lines else "(none)"

    @staticmethod
    def _extract_json_block(raw: str) -> str:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in LLM response")
        return raw[start : end + 1]

    @staticmethod
    def _fix_escaped_quotes(json_str: str) -> str:
        """
        Fix improperly escaped quotes in JSON strings.

        LLMs sometimes output malformed JSON with backslash-quote instead of quote:
        "text": \\"This is a quote\\"

        This should be:
        "text": "This is a quote"

        This function attempts to fix such cases by replacing \" patterns
        in array values with proper quotes.
        """
        # Try to parse as-is first
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError:
            pass

        import re

        # Fix common issue: \" instead of " in array string values
        # Pattern: [ \"text\" ] should become [ "text" ]
        fixed = json_str

        # Replace \" after [ or , with just "
        # Pattern matches: [ followed by optional whitespace followed by \"
        fixed = re.sub(r'(\[)\s*\\"', r'\1 "', fixed)
        fixed = re.sub(r'(\,)\s*\\"', r'\1 "', fixed)

        # Replace \" before ] or , with just "
        # Pattern matches: \" followed by optional whitespace followed by ] or ,
        fixed = re.sub(r'\\"(\s*)(\])', r'"\1\2', fixed)
        fixed = re.sub(r'\\"(\s*)(\,)', r'"\1\2', fixed)

        # Now try parsing again
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            # If still failing, return original and let error handling deal with it
            return json_str

    def _parse_llm_response(
        self,
        response_text: str,
        *,
        chunk_index: int,
        chunk_text: str,
        source_alias: str | None,
        source_definition_id: int | None,
        retrieval_alias_map: dict[str, str] | None = None,
    ) -> ChunkAnalysisResult:
        try:
            json_block = self._extract_json_block(response_text)
            # Try to fix common JSON issues from LLM responses
            fixed_json = self._fix_escaped_quotes(json_block)
            payload = json.loads(fixed_json)
            parsed = ArchitectLLMResponse.model_validate(payload)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "architect_parse_error: chunk=%d error=%s response=%s",
                chunk_index,
                exc,
                response_text,
            )
            parsed = ArchitectLLMResponse()
        return ChunkAnalysisResult(
            chunk_index=chunk_index,
            chunk_text=chunk_text,
            source_entity_alias=source_alias,
            source_entity_definition_id=source_definition_id,
            new_instances=parsed.new_instances,
            existing_instances=parsed.existing_instances,
            retrieval_alias_map=retrieval_alias_map or {},
        )

    def _aggregate_results(
        self, chunk_results: list[ChunkAnalysisResult]
    ) -> list[dict[str, Any]]:
        aggregated: dict[tuple[Any, ...], dict[str, Any]] = {}
        for result in chunk_results:
            for item in result.new_instances:
                canonical_alias = self._canonical_alias(item.alias)
                if not canonical_alias:
                    continue
                key = (
                    ArchitectProposalType.NEW_INSTANCE,
                    canonical_alias,
                )
                record = aggregated.setdefault(
                    key,
                    {
                        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
                        "entity_definition_id": item.entity_definition_id,
                        "canonical_alias": canonical_alias,
                        "entity_instance_id": None,
                        "alias": item.alias,
                        "confidence_scores": [],
                        "justifications": [],
                        "metadata_snippets": [],
                        "chunks": [],
                        "alias_variants": set(),
                        "definition_votes": {},
                        "best_alias": item.alias,
                        "best_alias_confidence": item.confidence,
                    },
                )
                record["alias_variants"].add(item.alias)
                votes = record["definition_votes"].setdefault(
                    item.entity_definition_id,
                    {"count": 0, "confidences": []},
                )
                votes["count"] += 1
                if item.confidence is not None:
                    record["confidence_scores"].append(item.confidence)
                    votes["confidences"].append(item.confidence)
                    best_conf = record.get("best_alias_confidence")
                    if best_conf is None or item.confidence > best_conf:
                        record["best_alias"] = item.alias
                        record["best_alias_confidence"] = item.confidence
                if item.justification:
                    record["justifications"].append(item.justification)
                if item.metadata:
                    record["metadata_snippets"].append(item.metadata)
                # Store chunk text for later use in step 2
                if result.chunk_text not in record["chunks"]:
                    record["chunks"].append(result.chunk_text)

            for item in result.existing_instances:
                key = (
                    ArchitectProposalType.UPDATE_INSTANCE,
                    item.entity_instance_id,
                )
                record = aggregated.setdefault(
                    key,
                    {
                        "proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
                        "entity_definition_id": item.entity_definition_id,
                        "entity_instance_id": item.entity_instance_id,
                        "alias": None,
                        "confidence_scores": [],
                        "justifications": [],
                        "metadata_snippets": [],
                        "chunks": [],
                    },
                )
                if item.confidence is not None:
                    record["confidence_scores"].append(item.confidence)
                if item.justification:
                    record["justifications"].append(item.justification)
                if item.metadata:
                    alias_hint = (
                        item.metadata.get("alias")
                        if isinstance(item.metadata, dict)
                        else None
                    )
                    if alias_hint and not record.get("alias"):
                        record["alias"] = alias_hint
                    record["metadata_snippets"].append(item.metadata)
                # Store chunk text for later use in step 2
                if result.chunk_text not in record["chunks"]:
                    record["chunks"].append(result.chunk_text)

                # Fallback: use retrieval_alias_map if alias is not set
                if (
                    not record.get("alias")
                    and item.entity_instance_id in result.retrieval_alias_map
                ):
                    record["alias"] = result.retrieval_alias_map[
                        item.entity_instance_id
                    ]

        proposals: list[dict[str, Any]] = []
        for record in aggregated.values():
            confidence = (
                sum(record["confidence_scores"]) / len(record["confidence_scores"])
                if record["confidence_scores"]
                else None
            )
            justification = (
                "\n".join(dict.fromkeys(record["justifications"]))
                if record["justifications"]
                else None
            )
            metadata = (
                self._merge_metadata(record["metadata_snippets"])
                if record["metadata_snippets"]
                else None
            )
            candidate_definitions: list[dict[str, Any]] = []
            best_definition_id = record.get("entity_definition_id")
            best_definition_score = (-1.0, -1)
            if record["proposal_type"] == ArchitectProposalType.NEW_INSTANCE:
                for definition_id, stats in record["definition_votes"].items():
                    avg_conf = (
                        sum(stats["confidences"]) / len(stats["confidences"])
                        if stats["confidences"]
                        else 0.0
                    )
                    vote_tuple = (avg_conf, stats["count"])
                    candidate_definitions.append(
                        {
                            "definition_id": definition_id,
                            "average_confidence": avg_conf,
                            "occurrences": stats["count"],
                        }
                    )
                    if vote_tuple > best_definition_score:
                        best_definition_score = vote_tuple
                        best_definition_id = definition_id
            alias_value = record.get("best_alias") or record.get("alias")
            variants_source = record.get("alias_variants")
            if variants_source:
                alias_variants = sorted(variants_source)
            elif alias_value:
                alias_variants = [alias_value]
            else:
                alias_variants = []
            canonical_alias = record.get("canonical_alias", "")
            if not canonical_alias and alias_value:
                canonical_alias = self._canonical_alias(alias_value)
            proposals.append(
                {
                    "proposal_type": record["proposal_type"],
                    "entity_definition_id": best_definition_id,
                    "entity_instance_id": record.get("entity_instance_id"),
                    "alias": alias_value,
                    "confidence": confidence,
                    "justification": justification,
                    "proposal_metadata": metadata,
                    "chunks": record["chunks"],
                    "canonical_alias": canonical_alias or "",
                    "alias_variants": alias_variants,
                    "candidate_definitions": candidate_definitions,
                    "mention_count": len(record["chunks"]),
                }
            )

        return proposals

    @staticmethod
    def _merge_metadata(snippets: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for snippet in snippets:
            for key, value in snippet.items():
                if isinstance(value, list):
                    merged.setdefault(key, [])
                    for item in value:
                        if item not in merged[key]:
                            merged[key].append(item)
                else:
                    merged[key] = value
        return merged

    @staticmethod
    def _normalize_alias(alias: str | None) -> str:
        if alias is None:
            return ""
        return " ".join(alias.strip().lower().split())

    _TITLE_PREFIXES = {
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
    _LEADING_ARTICLES = {"the", "a", "an"}

    def _canonical_alias(self, alias: str | None) -> str:
        if not alias:
            return ""
        value = alias.strip().lower()
        value = value.strip("\"'")
        value = re.sub(r"\([^()]*\)", " ", value)
        if ":" in value:
            parts = [part.strip() for part in value.split(":") if part.strip()]
            if parts:
                value = parts[-1]
        if "," in value:
            value = value.split(",", 1)[0].strip()
        words = value.split()
        while words and words[0].rstrip(".") in self._TITLE_PREFIXES:
            words.pop(0)
        if words and words[0] in self._LEADING_ARTICLES:
            words.pop(0)
        value = " ".join(words)
        value = re.sub(r"[^a-z0-9\s-]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _strip_html(self, text: str | None) -> str:
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

    def _postprocess_proposals(
        self, proposals: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not proposals:
            return []

        update_aliases: set[str] = set()
        for proposal in proposals:
            alias_value = proposal.get("alias")
            if isinstance(alias_value, str):
                trimmed = alias_value.strip()
                proposal["alias"] = trimmed or None
            else:
                proposal["alias"] = None

            canonical_alias = proposal.get("canonical_alias")
            if not canonical_alias and proposal.get("alias"):
                canonical_alias = self._canonical_alias(proposal["alias"])
            proposal["canonical_alias"] = canonical_alias or ""

            if proposal[
                "proposal_type"
            ] == ArchitectProposalType.UPDATE_INSTANCE and proposal.get(
                "canonical_alias"
            ):
                update_aliases.add(proposal["canonical_alias"])

            proposal.pop("evidence", None)

        best_new: dict[str, dict[str, Any]] = {}
        cleaned: list[dict[str, Any]] = []

        for proposal in proposals:
            canonical_alias = proposal.get("canonical_alias", "")

            if proposal["proposal_type"] == ArchitectProposalType.NEW_INSTANCE:
                if not canonical_alias:
                    continue
                if canonical_alias in update_aliases:
                    continue
                if self._should_discard_new_proposal(proposal):
                    continue
                current = best_new.get(canonical_alias)
                current_conf = current.get("confidence") if current else None
                new_conf = proposal.get("confidence")
                if current is None or (new_conf or 0) > (current_conf or 0):
                    best_new[canonical_alias] = proposal
            else:
                cleaned.append(proposal)

        cleaned.extend(best_new.values())
        return cleaned

    def _should_discard_new_proposal(self, proposal: dict[str, Any]) -> bool:
        mention_count = proposal.get("mention_count") or 0
        confidence = proposal.get("confidence") or 0.0
        alias = proposal.get("canonical_alias") or ""
        if mention_count <= 1 and confidence < 0.6:
            return True
        if len(alias) <= 3 and mention_count <= 1 and confidence < 0.8:
            return True
        return False
