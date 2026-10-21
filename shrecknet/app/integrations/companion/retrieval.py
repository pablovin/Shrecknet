"""Five bounded retrieval adapters; failures become independent diagnostics."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
import ipaddress
import socket
import time
from urllib.parse import urlsplit
import httpx
from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.services.character_agent_service import CharacterAgentService
from app.services.character_memory_service import CharacterMemoryService
from app.services.companion_authorization import grant
from app.schemas.character_companion import Evidence
from app.models.character_companion import utcnow

CATEGORIES = {"user_attachment": "user_provided", "atlas": "canonical_world", "shreckworld": "document_evidence", "chronicle": "chronicle_record", "web": "external_web", "character_memory": "subjective_memory"}


def evidence(tool, identifier, text, *, title="Source", reference=None, scope=None, rank=1, structured=None):
    reference, scope = reference or {}, scope or {}
    stable_reference = {key:value for key,value in reference.items() if key not in {"score","rank","relevance","retrieved_at"}}
    digest = hashlib.sha256((str(identifier) + str(stable_reference) + text).encode()).hexdigest()[:20]
    return Evidence(evidence_id=f"{tool}:{identifier}:{digest}", source_id=str(identifier), source_type=tool,
                    title=title, excerpt=text[:8000], structured_evidence=structured, reference=reference,
                    relevance={"rank": rank, "score": None, "method": "source_ranking"}, coverage={"status": "partial", "warnings": []},
                    epistemic_category=CATEGORIES[tool], retrieved_at=utcnow().isoformat(), scope=scope,
                    navigation={"kind": tool, "resource_id": str(identifier), "location": reference}).model_dump()


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
        self.scope = turn.request.get("context") or {}

    async def cached(self, tool, query, previous):
        """Reuse bounded same-chat evidence only after current-source validation."""
        if not previous or previous["scope"] != self.scope:
            return None
        age = (utcnow() - previous["created_at"].replace(tzinfo=utcnow().tzinfo)).total_seconds()
        normalized = query.casefold().strip()
        followup = normalized.startswith(("what about ", "tell me more", "explain that", "why is that", "and then"))
        if age > 300 or not (normalized == previous["query"].casefold().strip() or followup):
            return None
        rows = [row for row in previous["evidence"] if row.get("source_type") == tool][:6]
        if not rows:
            return None
        try:
            await asyncio.wait_for(self.validate_sources(rows), self.settings.companion_tool_timeout_s)
        except Exception:
            return None
        return {"tool": tool, "status": "ok", "evidence": rows, "elapsed_ms": 0, "coverage": "partial", "cache_hit": True}

    async def retrieve(self, tool, query):
        start = time.monotonic()
        try:
            rows = await asyncio.wait_for(getattr(self, tool)(query), timeout=self.settings.companion_tool_timeout_s)
            return {"tool": tool, "status": "ok" if rows else "empty", "evidence": rows[:6], "elapsed_ms": round((time.monotonic()-start)*1000), "coverage": "partial"}
        except asyncio.TimeoutError:
            return {"tool": tool, "status": "timed_out", "evidence": [], "coverage": "unknown"}
        except Exception:
            return {"tool": tool, "status": "unavailable", "evidence": [], "coverage": "unknown"}

    async def character_memory(self, query):
        async with get_driver().session(database=self.settings.neo4j_database) as graph:
            characters = CharacterAgentService(None, graph)
            rows = await CharacterMemoryService(characters).retrieve(self.turn.character_agent_id, query,
                public_only=str(getattr(self.actor.role, "value", self.actor.role)) != "admin")
        return [evidence("character_memory", row["id"], row.get("perspective") or "", title="Subjective character memory",
                         reference={key: row.get(key) for key in ("id", "scene_id", "source_type", "updated_at")}, scope={"character_agent_id": self.turn.character_agent_id, "ontology_id": self.scope.get("character_ontology_id", self.scope["ontology_id"])},
                         structured={key: row.get(key, []) for key in ("emotions", "beliefs", "impacts")}) for row in rows]

    async def atlas(self, query):
        from app.integrations.retrieval.neo4j_retriever import HybridNeo4jGraphRetriever
        from app.jobs.elder.v2_schemas import RetrievalPlan, RetrievalStep
        from app.services.elder_retrieval_service import retrieve_plan
        @asynccontextmanager
        async def factory():
            async with get_driver().session(database=self.settings.neo4j_database) as graph:
                yield graph
        retriever = HybridNeo4jGraphRetriever(session_factory=factory)
        plan = RetrievalPlan(answer_goal=query, steps=[RetrievalStep(id="companion", operation="hybrid_search", query=query, limit=6)])
        rows, _, _ = await retrieve_plan(retriever, plan=plan, ontology_ids=[self.scope["ontology_id"]], instance_id=None, candidate_limit=30, rerank_limit=6)
        return [evidence("atlas", row.node_id, row.text, title=getattr(row, "name", None) or "Atlas record",
                         reference={"node_id": row.node_id, "ontology_id": self.scope["ontology_id"], "node_label": row.node_label}, scope={"ontology_id": self.scope["ontology_id"]})
                for row in rows.get("companion", [])]

    async def shreckworld(self, query):
        identifier = self.scope.get("shreckworld_id")
        if not identifier or not self.settings.companion_shreckworld_url:
            return []
        payload = {"query": query[:4000], "mode": "evidence", "retrieval_strategy": "single", "top_k": 6}
        if self.scope.get("library_item_ids"):
            payload["library_item_ids"] = self.scope["library_item_ids"]
        async with httpx.AsyncClient(base_url=self.settings.companion_shreckworld_url, follow_redirects=False) as client:
            response = await client.post(f"/shreckworlds/{identifier}/companion-evidence", json=payload, headers={"Authorization": f"Bearer {grant(self.turn, 'shreckworld')}"})
            response.raise_for_status()
            data = response.json()
        return [evidence("shreckworld", str(row.get("library_item_id", identifier)) + ":" + str(row.get("chunk_id") or row.get("id") or i), row.get("excerpt") or row.get("text") or "", title=row.get("title") or "Book passage",
                         reference=row, scope={"shreckworld_id": identifier}, rank=i) for i, row in enumerate(data.get("citations") or data.get("evidence") or [], 1)]

    async def chronicle(self, query):
        if not self.settings.companion_chronicles_url:
            return []
        payload = {"query": query[:4000], "ontology_id": self.scope.get("chronicle_ontology_id"), "limit": 6,
                   "chronicle_ids": [self.scope["chronicle_id"]] if self.scope.get("chronicle_id") else None}
        async with httpx.AsyncClient(base_url=self.settings.companion_chronicles_url, follow_redirects=False) as client:
            response = await client.post("/chronicles/companion-search", json=payload, headers={"Authorization": f"Bearer {grant(self.turn, 'chronicle')}"})
            response.raise_for_status()
            data = response.json()
        return [evidence("chronicle", row["chronicle_id"], row["excerpt"], title=row.get("title", "Chronicle"), reference=row, scope={"ontology_id": self.scope.get("ontology_id")}, rank=i) for i, row in enumerate(data["items"], 1)]

    async def web(self, query):
        if not self.settings.companion_web_enabled or not self.settings.companion_web_search_url:
            return []
        # Configured retrieval provider must return {results:[{title,url,snippet}]}.
        # No page fetching: unsafe result URLs are discarded, not followed.
        headers = {"Authorization": f"Bearer {self.settings.companion_web_search_token}"} if self.settings.companion_web_search_token else {}
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(self.settings.companion_web_search_url, json={"query": query[:500], "limit": 6}, headers=headers)
            response.raise_for_status()
            data = response.json()
        rows = []
        for row in data.get("results", [])[:6]:
            if await safe_web_url(row.get("url", "")):
                rows.append(evidence("web", row["url"], row.get("snippet", "")[:4000], title=row.get("title", "Web result"), reference={"url": row["url"]}))
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
                        else:
                            result=await graph.run('MATCH (n) WHERE (n:EntityInstance OR n:Scene OR n:Milestone) AND n.ontology_id=$ontology AND coalesce(n.entity_instance_id,n.scene_id,n.milestone_id,n.id)=$id RETURN n.updated_at AS revision',ontology=self.scope['ontology_id'],id=ref['node_id'])
                        row=await result.single()
                        if not row or (ref.get('updated_at') and str(row['revision'])!=str(ref['updated_at'])):
                            raise ValueError('source_changed')
            elif tool in {'chronicle','shreckworld'}:
                base=self.settings.companion_chronicles_url if tool=='chronicle' else self.settings.companion_shreckworld_url
                path='/chronicles/companion-validate' if tool=='chronicle' else f"/shreckworlds/{self.scope['shreckworld_id']}/companion-validate"
                async with httpx.AsyncClient(base_url=base,timeout=self.settings.companion_tool_timeout_s,follow_redirects=False) as client:
                    response=await client.post(path,json={'references':refs},headers={'Authorization':f'Bearer {grant(self.turn,tool)}'})
                    response.raise_for_status()
