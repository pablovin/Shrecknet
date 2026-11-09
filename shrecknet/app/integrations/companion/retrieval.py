"""Five bounded retrieval adapters; failures become independent diagnostics."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
import httpx
from app.core.config_store import get_settings, COMPANION_TOOL_EVIDENCE_LIMIT
from app.graph.neo4j import get_driver
from app.services.character_agent_service import CharacterAgentService
from app.services.character_memory_service import CharacterMemoryService
from app.services.companion_authorization import grant
from app.schemas.character_companion import Evidence, SourceReadinessRead
from app.models.character_companion import utcnow
from app.integrations.companion.atlas_sources import atlas_revision, read_atlas_source

CATEGORIES = {"foundry": "user_provided", "user_attachment": "user_provided", "atlas": "canonical_world", "shreckworld": "document_evidence", "chronicle": "chronicle_record", "web": "external_web", "character_memory": "subjective_memory"}
logger = logging.getLogger(__name__)


@dataclass
class ScopedEvidence:
    rows: list[dict]
    source_coverage: dict[str, int]
    trace: dict | None = None
    source_attention: list[dict] | None = None


def evidence(tool, identifier, text, *, title="Source", reference=None, scope=None, rank=1, structured=None):
    reference, scope = reference or {}, scope or {}
    stable_reference = {key:value for key,value in reference.items() if key not in {"score","rank","relevance","retrieved_at"}}
    digest = hashlib.sha256((str(identifier) + str(stable_reference) + text).encode()).hexdigest()[:20]
    return Evidence(evidence_id=f"{tool}:{identifier}:{digest}", source_id=str(identifier), source_type=tool,
                    title=title, excerpt=text[:8000], structured_evidence=structured, reference=reference,
                    relevance={"rank": rank, "score": None, "method": "source_ranking"}, coverage={"status": "partial", "warnings": []},
                    epistemic_category=CATEGORIES[tool], retrieved_at=utcnow().isoformat(), scope=scope,
                    navigation={"kind": tool, "resource_id": str(identifier), "location": reference}).model_dump()


def provider_web_enabled(settings):
    target = settings.model_companion_web_search
    return bool(target.web_search_enabled and target.provider == 'openrouter' and target.name)


def web_available(settings):
    return bool(settings.companion_web_search_url) or provider_web_enabled(settings)


async def safe_web_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (ValueError, OSError):
        return False


class CompanionRetrieval:
    def __init__(self, turn, actor):
        self.turn, self.actor, self.settings = turn, actor, get_settings()
        self.llm_usage = []
        self.scope = getattr(turn, "execution_context", None) if getattr(turn, "execution_context", None) is not None else (turn.request.get("context") or {})

    async def retrieve(self, tool, query, *, planned_call=None):
        start = time.monotonic()
        try:
            options = {'planned_call': planned_call} if tool in {'atlas', 'shreckworld'} else {}
            timeout = self.settings.companion_web_timeout_s if tool == 'web' else self.settings.companion_tool_timeout_s
            result = await asyncio.wait_for(getattr(self, tool)(query, **options), timeout=timeout)
            rows = result.rows if isinstance(result, ScopedEvidence) else result
            response = {"tool": tool, "status": "ok" if rows else "empty", "evidence": rows[:COMPANION_TOOL_EVIDENCE_LIMIT], "elapsed_ms": round((time.monotonic()-start)*1000), "coverage": "partial", "timeout_s": timeout}
            if isinstance(result, ScopedEvidence):
                response["source_coverage"] = result.source_coverage
                response['sources_requiring_attention'] = result.source_attention or []
                if result.trace is not None:
                    response["source_trace"] = result.trace
                if result.source_coverage["sources_requiring_reindex"]:
                    response.update(coverage='partial', source_readiness='partial', reason="incompatible_index")
                elif result.source_coverage["sources_without_ready_index"]:
                    response.update(coverage='partial', source_readiness='partial', reason="index_not_ready")
                elif result.source_attention:
                    response.update(coverage='partial', source_readiness='partial')
                else:
                    response['source_readiness'] = 'ready'
            if len(rows) > COMPANION_TOOL_EVIDENCE_LIMIT:
                response['evidence_omissions'] = [{'evidence_id': row['evidence_id'], 'reason':'tool_record_limit'} for row in rows[COMPANION_TOOL_EVIDENCE_LIMIT:]]
            return response
        except asyncio.TimeoutError:
            return {"tool": tool, "status": "timed_out", "evidence": [], "coverage": "unknown", "timeout_s": timeout, "timeout_layer": "tool"}
        except httpx.TimeoutException:
            return {"tool": tool, "status": "timed_out", "evidence": [], "coverage": "unknown", "timeout_s": timeout, "timeout_layer": "http"}
        except Exception as exc:
            logger.warning("Companion retrieval %s failed: %s", tool, type(exc).__name__)
            return {"tool": tool, "status": "unavailable", "evidence": [], "coverage": "unknown"}

    async def character_memory(self, query):
        async with get_driver().session(database=self.settings.neo4j_database) as graph:
            characters = CharacterAgentService(None, graph)
            rows = await CharacterMemoryService(characters).retrieve(self.turn.character_agent_id, query,
                public_only=str(getattr(self.actor.role, "value", self.actor.role)) != "admin")
        return [evidence("character_memory", row["id"], row.get("perspective") or "", title="Subjective character memory",
                         reference={key: row.get(key) for key in ("id", "scene_id", "source_type", "updated_at")}, scope={"character_agent_id": self.turn.character_agent_id, "ontology_id": self.scope.get("character_ontology_id") or self.scope.get("ontology_id")},
                         structured={key: row.get(key, []) for key in ("emotions", "beliefs", "impacts")}) for row in rows]

    async def atlas(self, query, *, planned_call=None):
        if not self.scope.get("ontology_id"):
            return []
        from app.integrations.retrieval.neo4j_retriever import HybridNeo4jGraphRetriever
        from app.services.elder_retrieval_service import retrieve_plan
        @asynccontextmanager
        async def factory():
            async with get_driver().session(database=self.settings.neo4j_database) as graph:
                yield graph
        retriever = HybridNeo4jGraphRetriever(session_factory=factory, embedding_timeout_s=self.settings.companion_tool_timeout_s)
        from app.jobs.elder.planner import create_retrieval_plan, single_query_plan, validate_bounded_cypher
        from app.integrations.llm.shreckllm_client import ShreckLLMClient
        if planned_call is not None and planned_call.atlas_plan is not None:
            plan = planned_call.atlas_plan.model_copy(deep=True)
        elif planned_call is not None and planned_call.retrieval_depth == 'deep':
            async with ShreckLLMClient(base_url=self.settings.shreckllm_base_url,
                                  timeout=self.settings.companion_tool_timeout_s, max_retries=0) as client:
                plan = await create_retrieval_plan(llm_client=client,
                    model=self.settings.model_elder_planner.model_copy(update={"web_search_enabled":False}),
                    repair_model=self.settings.model_agents_repair_json.model_copy(update={"web_search_enabled":False}),
                    query=query, grounding={"ontology_ids":[self.scope["ontology_id"]]},
                    usage_tag="companion.elder_planning")
        else:
            plan = single_query_plan(query)
        plan = validate_bounded_cypher(plan, [self.scope['ontology_id']])
        rows, _, _ = await retrieve_plan(retriever, plan=plan, ontology_ids=[self.scope["ontology_id"]], instance_id=None, candidate_limit=30, rerank_limit=COMPANION_TOOL_EVIDENCE_LIMIT)
        records = list({row.node_id:row for values in rows.values() for row in values}.values())
        fields = {}
        if self.settings.companion_rich_response_active:
            from app.db.session import AsyncSessionMaker
            from app.integrations.companion.atlas_properties import fields_for
            # This call owns its session; parallel adapters never share it.
            try:
                async with AsyncSessionMaker() as session:
                    for row in records:
                        fields[row.node_id] = await fields_for(row, self.scope['ontology_id'], session)
            except Exception:
                fields = {}  # Optional structured presentation cannot fail text retrieval.
        projected = []
        for row in records:
            structured = {'fields': fields[row.node_id]} if fields.get(row.node_id) else {}
            description = row.properties.get('description')
            if isinstance(description, str) and 0 < len(description) <= 300:
                structured['description'] = description
            revision = atlas_revision(row.properties, row.node_label)
            projected.append(evidence("atlas", row.node_id, row.text,
                title=getattr(row, "node_name", None) or getattr(row, "node_alias", None) or "Atlas record",
                reference={"node_id": row.node_id, "ontology_id": self.scope["ontology_id"], "node_label": row.node_label,
                           **({'updated_at': str(revision)} if revision else {})},
                scope={"ontology_id": self.scope["ontology_id"]}, structured=structured or None))
        return projected

    async def shreckworld(self, query, *, planned_call=None):
        identifier = self.scope.get("shreckworld_id") or self.scope.get("ontology_id")
        direct = not self.scope.get("shreckworld_id")
        path = f"/ontologies/{identifier}" if direct else f"/shreckworlds/{identifier}"
        if not identifier or not self.settings.shreckworld_base_url:
            return []
        strategy = 'planned' if planned_call is not None and planned_call.retrieval_depth == 'deep' else 'single'
        payload = {"query": query[:4000], "mode": "evidence", "retrieval_strategy": strategy, "top_k": COMPANION_TOOL_EVIDENCE_LIMIT}
        if self.scope.get("library_item_ids"):
            payload["library_item_ids"] = self.scope["library_item_ids"]
        async with httpx.AsyncClient(base_url=self.settings.shreckworld_base_url, timeout=self.settings.companion_tool_timeout_s, follow_redirects=False) as client:
            response = await client.post(f"{path}/companion-evidence", json=payload, headers={"Authorization": f"Bearer {grant(self.turn, 'shreckworld')}"})
            response.raise_for_status()
            data = response.json()
        planned = payload["retrieval_strategy"] == "planned"
        trace = {key:data.get(key) for key in ("query_plan", "evidence_coverage", "evidence_groups", "v3_trace")} if planned else None
        rows = []
        for i, row in enumerate(data.get("citations") or data.get("evidence") or [], 1):
            text = (row.get("evidence_text") if planned else None) or row.get("excerpt") or row.get("text") or ""
            if planned and len(text) > 8000:
                trace["omitted_oversized_units"] = trace.get("omitted_oversized_units", 0) + 1
                trace.setdefault('evidence_omissions', []).append({
                    'source_id': row.get('source_id'), 'library_item_id': row.get('library_item_id'),
                    'reason':'oversized_evidence_unit', 'max_characters':8000})
                continue
            groups = [g for g in data.get("evidence_groups", []) if g["evidence_id"] == row.get("source_id")]
            rows.append(evidence("shreckworld", str(row.get("library_item_id", identifier)) + ":" + str(row.get("chunk_id") or row.get("id") or i),
                text, title=row.get("title") or "Book passage", reference=row,
                scope={"ontology_id" if direct else "shreckworld_id": identifier}, rank=i,
                structured={"evidence_groups":groups} if planned else None))
        coverage = data["source_coverage"]
        keys = ("requested_source_count", "searched_source_count", "sources_without_ready_index", "sources_requiring_reindex")
        if any(type(coverage[key]) is not int or coverage[key] < 0 for key in keys):
            raise ValueError("invalid_source_coverage")
        if coverage['searched_source_count'] > coverage['requested_source_count'] or any(coverage[key] > coverage['requested_source_count'] for key in keys[2:]):
            raise ValueError('invalid_source_coverage')
        attention = []
        for item in data.get('sources_requiring_attention') or []:
            attention.append(SourceReadinessRead.model_validate(item).model_dump())
        return ScopedEvidence(rows, {key: coverage[key] for key in keys}, trace, attention)

    async def chronicle(self, query):
        if not self.settings.shreckrpg_base_url:
            return []
        payload = {"query": query[:4000], "ontology_id": self.scope.get("chronicle_ontology_id"), "limit": COMPANION_TOOL_EVIDENCE_LIMIT,
                   "chronicle_ids": [self.scope["chronicle_id"]] if self.scope.get("chronicle_id") else None}
        async with httpx.AsyncClient(base_url=self.settings.shreckrpg_base_url, timeout=self.settings.companion_tool_timeout_s, follow_redirects=False) as client:
            response = await client.post("/chronicles/companion-search", json=payload, headers={"Authorization": f"Bearer {grant(self.turn, 'chronicle')}"})
            response.raise_for_status()
            data = response.json()
        return [evidence("chronicle", row["chronicle_id"], row["excerpt"], title=row.get("title", "Chronicle"), reference=row, scope={"ontology_id": self.scope.get("ontology_id")}, rank=i) for i, row in enumerate(data["items"], 1)]

    async def web(self, query):
        if not web_available(self.settings):
            return []
        if not self.settings.companion_web_search_url:
            from app.integrations.llm.shreckllm_client import ShreckLLMClient
            from app.jobs.companion.prompts import WEB_RETRIEVAL
            async with ShreckLLMClient(base_url=self.settings.shreckllm_base_url,
                                      timeout=getattr(self.settings, "companion_web_timeout_s", self.settings.companion_tool_timeout_s), max_retries=0) as client:
                data = await client.chat(model=self.settings.model_companion_web_search,
                    messages=[{"role": "system", "content": WEB_RETRIEVAL}, {"role": "user", "content": query[:500]}],
                    usage_tag="companion.web_retrieval", max_tokens=1000, return_metadata=True)
                self.llm_usage.append({"stage": "companion.web_retrieval", "usage": data.get("usage") or {}})
            results = [row["url_citation"] for row in data.get("annotations", []) if row.get("type") == "url_citation"]
            rows = []
            for row in results[:COMPANION_TOOL_EVIDENCE_LIMIT]:
                # Only actual provider excerpts are evidence; generated summaries are never sources.
                if row.get("content") and await safe_web_url(row.get("url", "")):
                    rows.append(evidence("web", row["url"], row["content"][:4000], title=row.get("title", "Web result"), reference={"url": row["url"]}))
            return rows
        # Configured retrieval provider must return {results:[{title,url,snippet}]}.
        # No page fetching: unsafe result URLs are discarded, not followed.
        headers = {"Authorization": f"Bearer {self.settings.companion_web_search_token}"} if self.settings.companion_web_search_token else {}
        async with httpx.AsyncClient(timeout=self.settings.companion_web_timeout_s, follow_redirects=False) as client:
            response = await client.post(self.settings.companion_web_search_url, json={"query": query[:500], "limit": COMPANION_TOOL_EVIDENCE_LIMIT, **({"include_images": True} if self.settings.companion_rich_response_active and self.settings.companion_web_images_enabled else {})}, headers=headers)
            response.raise_for_status()
            data = response.json()
        rows = []
        for row in data.get("results", [])[:COMPANION_TOOL_EVIDENCE_LIMIT]:
            if await safe_web_url(row.get("url", "")):
                images = []
                if self.settings.companion_rich_response_active and self.settings.companion_web_images_enabled:
                    for image in (row.get('images') or [])[:4]:
                        if (isinstance(image,dict) and image.get('source_page_url') == row['url'] and
                            image.get('relation') == 'page_image' and isinstance(image.get('attribution'),str) and
                            image['attribution'].strip() and isinstance(image.get('url'),str) and await safe_web_url(image['url'])):
                            images.append({'url':image['url'],'attribution':image['attribution'][:1000]})
                rows.append(evidence("web", row["url"], row.get("snippet", "")[:4000], title=row.get("title", "Web result"), reference={"url": row["url"], **({'images':images} if images else {})}))
        return rows

    async def validate_sources(self, sources):
        """Recheck cited records after synthesis without another model call."""
        for tool in {source['source_type'] for source in sources}:
            refs=[source['reference'] for source in sources if source['source_type']==tool]
            if tool in {'atlas','character_memory'}:
                async with get_driver().session(database=self.settings.neo4j_database) as graph:
                    for ref in refs:
                        if tool=='character_memory':
                            result=await graph.run('MATCH (a:CharacterAgent {id:$agent})-[:HAS_PERSPECTIVE]->(p:ScenePerspective {id:$id}) RETURN p.updated_at AS revision',agent=self.turn.character_agent_id,id=ref['id'])
                            row=await result.single()
                        else:
                            row=await read_atlas_source(graph, ref, self.scope['ontology_id'])
                        if not row or (ref.get('updated_at') and str(row['revision'])!=str(ref['updated_at'])):
                            raise ValueError('source_changed')
            elif tool in {'chronicle','shreckworld'}:
                base=self.settings.shreckrpg_base_url if tool=='chronicle' else self.settings.shreckworld_base_url
                path='/chronicles/companion-validate' if tool=='chronicle' else (f"/shreckworlds/{self.scope['shreckworld_id']}/companion-validate" if self.scope.get("shreckworld_id") else f"/ontologies/{self.scope['ontology_id']}/companion-validate")
                async with httpx.AsyncClient(base_url=base,timeout=self.settings.companion_tool_timeout_s,follow_redirects=False) as client:
                    response=await client.post(path,json={'references':refs},headers={'Authorization':f'Bearer {grant(self.turn,tool)}'})
                    response.raise_for_status()
