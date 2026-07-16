"""Versioned retrieval strategies for Librarian PDF question answering."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from typing import Any, AsyncIterator, Callable

from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.graphrag.embedding_runtime import get_ready_embedding_runtime

logger = logging.getLogger(__name__)

BRANCH_LIMIT = 40
RRF_K = 60
RERANK_LIMIT = 25
DEFAULT_FINAL_K = 6
MAX_PARENT_CHARS = 12_000
SIBLING_WINDOW_CHARS = 8_000
RERANKER_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def is_table_like_query(query: str) -> bool:
    return bool(re.search(
        r"\b(table|list|chart|occupation|occupations|skills?|weapons?|spells?|items?|equipment|character creation|creation)\b",
        query.casefold(),
    ))


class LibrarianRetrievalStrategyV2:
    """Rank-fusion retrieval over active child chunks with parent expansion."""

    name = "v2"
    fulltext_index_name = "pdf_chunk_context_fulltext_v2_idx"

    def __init__(self, *, session_factory: Callable[[], Any] | None = None) -> None:
        self.settings = get_settings()
        self._session_factory = session_factory
        self._cross_encoder: Any | None = None
        self._reranker_unavailable = False
        self._reranker_lock = threading.Lock()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        if self._session_factory is not None:
            async with self._session_factory() as session:
                yield session
            return
        driver = get_driver()
        async with driver.session(database=self.settings.neo4j_database) as session:
            yield session

    async def ensure_indexes(self) -> None:
        query = f"""
        CREATE FULLTEXT INDEX {self.fulltext_index_name} IF NOT EXISTS
        FOR (c:PdfChunk)
        ON EACH [c.book_title, c.rpg_system, c.heading_path_text,
                 c.primary_heading, c.display_text]
        """
        async with self._session() as session:
            await session.run(query)

    @staticmethod
    def extract_named_terms(query: str) -> list[str]:
        quoted = re.findall(r'["“]([^"”]{2,80})["”]', query)
        titled = re.findall(
            r"\b[A-Z][A-Za-z0-9'\-]*(?:\s+[A-Z][A-Za-z0-9'\-]*){0,3}\b", query
        )
        stop = {"What", "Which", "When", "Where", "How", "Give", "Explain", "Please", "Can", "Could", "The"}
        values: list[str] = []
        seen: set[str] = set()
        for value in quoted + titled:
            value = re.sub(r"\s+", " ", value).strip()
            if not value or value.split()[0] in stop or value.casefold() in seen:
                continue
            seen.add(value.casefold())
            values.append(value)
        return values[:8]

    @staticmethod
    def _lucene_query(query: str) -> str:
        tokens = re.findall(r"[\w'\-]+", query, re.UNICODE)[:20]
        escaped = [re.sub(r'([+\\&|!(){}\[\]^"~*?:/])', r"\\\1", token) for token in tokens]
        return " OR ".join(escaped) or query.strip()

    @staticmethod
    def _scope_where(alias: str = "c") -> str:
        return f"""
          coalesce({alias}.is_active, false) = true
          AND {alias}.chunk_role = 'child'
          AND coalesce({alias}.embedding_eligible, true) = true
          AND {alias}.ontology_id = $ontology_id
          AND (size($requested_ids) = 0 OR {alias}.library_item_id IN $requested_ids)
          AND {alias}.library_item_id IN $active_ids
        """

    async def _rows(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        async with self._session() as session:
            # Neo4j's run() first argument is also named ``query``. Passing a
            # Lucene parameter named ``query`` through **params collides with
            # that Python argument before Cypher executes, so always pass the
            # complete Cypher parameter map as the second positional argument.
            result = await session.run(cypher, params)
            return [dict(record) async for record in result]

    async def _vector(self, embedding: list[float], scope: dict[str, Any]) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.vector.queryNodes('pdf_chunk_text_vec_idx', $probe_limit, $embedding)
        YIELD node AS c, score
        WHERE {self._scope_where()}
        RETURN properties(c) AS props, score
        ORDER BY score DESC LIMIT $limit
        """
        return await self._rows(query, embedding=embedding, probe_limit=200, limit=BRANCH_LIMIT, **scope)

    async def _fulltext(self, query_text: str, scope: dict[str, Any]) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.fulltext.queryNodes('{self.fulltext_index_name}', $query)
        YIELD node AS c, score
        WHERE {self._scope_where()}
          AND coalesce(c.fulltext_eligible, true) = true
        RETURN properties(c) AS props, score
        ORDER BY score DESC LIMIT $limit
        """
        return await self._rows(query, query=self._lucene_query(query_text), limit=BRANCH_LIMIT, **scope)

    async def _exact(self, terms: list[str], scope: dict[str, Any]) -> list[dict[str, Any]]:
        if not terms:
            return []
        query = f"""
        MATCH (c:PdfChunk)
        WHERE {self._scope_where()}
          AND any(term IN $terms WHERE
            toLower(coalesce(c.primary_heading, '')) = toLower(term)
            OR toLower(coalesce(c.heading_path_text, '')) CONTAINS toLower(term)
            OR toLower(coalesce(c.display_text, '')) CONTAINS toLower(term))
        WITH c, size([term IN $terms WHERE
          toLower(coalesce(c.primary_heading, '')) = toLower(term) |
          term]) AS heading_exact
        RETURN properties(c) AS props, heading_exact AS score
        ORDER BY heading_exact DESC, c.chunk_index ASC LIMIT $limit
        """
        return await self._rows(query, terms=terms, limit=BRANCH_LIMIT, **scope)

    @staticmethod
    def reciprocal_rank_fusion(branches: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for branch, rows in branches.items():
            seen: set[str] = set()
            for rank, row in enumerate(rows, 1):
                props = dict(row.get("props") or {})
                chunk_id = str(props.get("chunk_id") or f"{props.get('library_item_id')}:{props.get('chunk_index')}")
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                candidate = merged.setdefault(chunk_id, {"props": props, "chunk_id": chunk_id, "rrf_score": 0.0, "branch_ranks": {}})
                candidate["rrf_score"] += 1.0 / (RRF_K + rank)
                candidate["branch_ranks"][branch] = rank
        return sorted(merged.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"]))

    def _load_reranker(self) -> Any | None:
        if self._reranker_unavailable:
            return None
        if self._cross_encoder is None:
            try:
                from sentence_transformers import CrossEncoder
                self._cross_encoder = CrossEncoder(
                    RERANKER_MODEL_ID,
                    device=self.settings.embedding_device,
                    automodel_args={"low_cpu_mem_usage": False},
                )
            except Exception as exc:
                self._reranker_unavailable = True
                logger.warning("librarian_v2_reranker_unavailable error=%s", exc)
        return self._cross_encoder

    async def _rerank(self, query: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        window = candidates[:RERANK_LIMIT]
        model = await asyncio.to_thread(self._load_reranker)
        if model is None:
            return candidates, True
        pairs = []
        for candidate in window:
            props = candidate["props"]
            context = "\n".join(filter(None, [props.get("book_title"), props.get("heading_path_text"), props.get("primary_heading"), props.get("display_text")]))
            pairs.append((query, context))
        try:
            scores = await asyncio.to_thread(self._predict, model, pairs)
        except Exception as exc:
            logger.warning("librarian_v2_rerank_failed error=%s", exc)
            return candidates, True
        for candidate, score in zip(window, scores):
            candidate["rerank_score"] = float(score)
        window.sort(key=lambda item: (-item["rerank_score"], -item["rrf_score"], item["chunk_id"]))
        return window + candidates[RERANK_LIMIT:], False

    def _predict(self, model: Any, pairs: list[tuple[str, str]]) -> Any:
        """Serialize inference on the shared model; PyTorch modules are not request-thread safe."""
        with self._reranker_lock:
            return model.predict(pairs)

    @staticmethod
    def _diverse(candidates: list[dict[str, Any]], target: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        selected: list[dict[str, Any]] = []
        exclusions: list[dict[str, str]] = []
        parent_counts: defaultdict[str, int] = defaultdict(int)
        item_counts: defaultdict[int, int] = defaultdict(int)
        for candidate in candidates:
            props = candidate["props"]
            parent = str(props.get("parent_chunk_id") or candidate["chunk_id"])
            item = int(props.get("library_item_id") or 0)
            text = re.sub(r"\s+", " ", str(props.get("display_text") or "")).casefold()
            if any(SequenceMatcher(None, text[:2000], old["_text"][:2000]).ratio() >= 0.92 for old in selected):
                exclusions.append({"chunk_id": candidate["chunk_id"], "reason": "near_duplicate"})
                continue
            if parent_counts[parent] >= 1 or (item_counts[item] >= 2 and len({int(x["props"].get("library_item_id") or 0) for x in candidates}) > 1):
                exclusions.append({"chunk_id": candidate["chunk_id"], "reason": "diversity_cap"})
                continue
            candidate["_text"] = text
            selected.append(candidate)
            parent_counts[parent] += 1
            item_counts[item] += 1
            if len(selected) >= target:
                break
        if len(selected) < target:
            selected_ids = {x["chunk_id"] for x in selected}
            for candidate in candidates:
                if candidate["chunk_id"] not in selected_ids:
                    candidate["_text"] = re.sub(r"\s+", " ", str(candidate["props"].get("display_text") or "")).casefold()
                    selected.append(candidate)
                    selected_ids.add(candidate["chunk_id"])
                    if len(selected) >= target:
                        break
        for candidate in selected:
            candidate.pop("_text", None)
        return selected, exclusions

    async def _expand(self, selected: list[dict[str, Any]], table_like: bool) -> list[dict[str, Any]]:
        ids = [candidate["chunk_id"] for candidate in selected]
        query = """
        UNWIND $ids AS selected_id
        MATCH (child:PdfChunk {chunk_id: selected_id})-[:CHILD_OF]->(parent:PdfChunk)
        OPTIONAL MATCH (sibling:PdfChunk)-[:CHILD_OF]->(parent)
        WHERE sibling.is_active = true AND sibling.chunk_role = 'child'
        WITH selected_id, child, parent, sibling ORDER BY sibling.chunk_index
        RETURN selected_id, properties(child) AS child, properties(parent) AS parent,
               collect(properties(sibling)) AS siblings
        """
        rows = await self._rows(query, ids=ids)
        by_id = {str(row["selected_id"]): row for row in rows}
        expanded: list[dict[str, Any]] = []
        for candidate in selected:
            row = by_id.get(candidate["chunk_id"])
            child = dict((row or {}).get("child") or candidate["props"])
            parent = dict((row or {}).get("parent") or child)
            siblings = list((row or {}).get("siblings") or [child])
            parent_text = str(parent.get("display_text") or "")
            mode = "complete_parent"
            incomplete = False
            if len(parent_text) > MAX_PARENT_CHARS and not table_like:
                mode = "sibling_window"
                ordered = sorted(siblings, key=lambda p: int(p.get("chunk_index") or 0))
                hit = next((i for i, value in enumerate(ordered) if value.get("chunk_id") == child.get("chunk_id")), 0)
                chosen = [ordered[hit]]
                left, right = hit - 1, hit + 1
                while len("\n\n".join(str(x.get("display_text") or "") for x in chosen)) < SIBLING_WINDOW_CHARS and (left >= 0 or right < len(ordered)):
                    if left >= 0:
                        chosen.insert(0, ordered[left]); left -= 1
                    if right < len(ordered):
                        chosen.append(ordered[right]); right += 1
                parent_text = "\n\n".join(str(x.get("display_text") or "") for x in chosen).strip()
            elif len(parent_text) > MAX_PARENT_CHARS and table_like:
                mode = "complete_table_parent"
                incomplete = len(parent_text) > 40_000
            pages = parent.get("physical_page_numbers") or child.get("physical_page_numbers") or child.get("page_numbers") or [child.get("page_number") or 1]
            labels = parent.get("displayed_page_labels") or child.get("displayed_page_labels") or []
            boxes = parent.get("bounding_boxes") or child.get("bounding_boxes") or []
            if isinstance(boxes, str):
                try: boxes = json.loads(boxes)
                except (TypeError, ValueError): boxes = []
            expanded.append({
                "library_item_id": int(child.get("library_item_id") or 0), "chunk_index": int(child.get("chunk_index") or 0),
                "chunk_id": str(child.get("chunk_id") or candidate["chunk_id"]), "parent_chunk_id": parent.get("chunk_id"),
                "page_number": int(pages[0] or 1), "physical_page_numbers": pages, "displayed_page_labels": labels,
                "display_page_label": next((str(x) for x in labels if x), None), "bounding_boxes": boxes,
                "text": parent_text, "matched_child_text": child.get("display_text") or "", "score": float(candidate.get("rerank_score", candidate["rrf_score"])),
                "rrf_score": candidate["rrf_score"], "rerank_score": candidate.get("rerank_score"), "branch_ranks": candidate["branch_ranks"],
                "book_title": child.get("book_title"), "expansion_mode": mode, "incomplete_evidence": incomplete,
            })
        return expanded

    async def retrieve(self, *, query: str, ontology_id: int, library_item_ids: list[int] | None,
                       active_library_item_ids: list[int], top_k: int, trace: list[dict[str, Any]] | None = None,
                       table_like: bool = False, **_: Any) -> list[dict[str, Any]]:
        if not active_library_item_ids:
            return []
        await self.ensure_indexes()
        original = query.strip()
        prefixed = original if original.startswith("query: ") else f"query: {original}"
        runtime = await get_ready_embedding_runtime()
        embedding = await runtime.embed_query(prefixed, request_id=f"librarian-v2:{ontology_id}:{abs(hash(original)) % 1000000}")
        scope = {"ontology_id": ontology_id, "requested_ids": library_item_ids or [], "active_ids": active_library_item_ids}
        terms = self.extract_named_terms(original)
        vector, fulltext, exact = await asyncio.gather(self._vector(embedding, scope), self._fulltext(original, scope), self._exact(terms, scope))
        branches = {"vector": vector, "fulltext": fulltext, "exact": exact}
        fused = self.reciprocal_rank_fusion(branches)
        reranked, fallback = await self._rerank(original, fused)
        effective_k = min(8, max(5, top_k or DEFAULT_FINAL_K))
        selected, exclusions = self._diverse(reranked, effective_k)
        expanded = await self._expand(selected, table_like)
        base_url = (self.settings.media_public_url or self.settings.media_base_url).rstrip("/")
        for chunk in expanded:
            pdf_url = f"{base_url}/library/{ontology_id}/{chunk['library_item_id']}/content.pdf"
            chunk["pdf_url"] = pdf_url
            chunk["page_url"] = f"{pdf_url}#page={chunk['page_number']}"
        if trace is not None:
            trace.extend([
                {"step": "v2_parallel_retrieve", "data": {"query": original, "embedding_query": prefixed, "named_terms": terms, "branch_counts": {k: len(v) for k, v in branches.items()}}},
                {"step": "v2_rrf_rerank", "data": {"rrf_k": RRF_K, "fused_count": len(fused), "rerank_window": min(RERANK_LIMIT, len(fused)), "reranker_fallback": fallback}},
                {"step": "v2_diversity_expand", "data": {"effective_top_k": effective_k, "selected": len(selected), "exclusions": exclusions, "expansion_modes": [x["expansion_mode"] for x in expanded]}},
            ])
        return expanded


_shared_strategy: LibrarianRetrievalStrategyV2 | None = None


def get_librarian_retrieval_strategy() -> LibrarianRetrievalStrategyV2:
    """Return the process-wide strategy so startup and requests share one reranker."""
    global _shared_strategy
    if _shared_strategy is None:
        _shared_strategy = LibrarianRetrievalStrategyV2()
    return _shared_strategy


async def preload_librarian_reranker() -> bool:
    """Load and warm the shared cross-encoder without making startup fatal."""
    strategy = get_librarian_retrieval_strategy()
    started = asyncio.get_running_loop().time()
    model = await asyncio.to_thread(strategy._load_reranker)
    if model is None:
        logger.warning("librarian_reranker_prewarm_failed model=%s", RERANKER_MODEL_ID)
        return False
    try:
        await asyncio.to_thread(strategy._predict, model, [("warmup query", "warmup passage")])
    except Exception as exc:
        strategy._reranker_unavailable = True
        strategy._cross_encoder = None
        logger.warning("librarian_reranker_prewarm_failed model=%s error=%s", RERANKER_MODEL_ID, exc)
        return False
    logger.info("librarian_reranker_prewarm_done model=%s elapsed_ms=%.1f",
                RERANKER_MODEL_ID, (asyncio.get_running_loop().time() - started) * 1000)
    return True
