"""Utility services for Neo4j graph maintenance."""

from __future__ import annotations

import logging

from neo4j import AsyncSession as AsyncNeo4jSession

logger = logging.getLogger(__name__)


class GraphMaintenanceService:
    """Provides administrative operations for maintaining the Neo4j graph."""

    def __init__(self, session: AsyncNeo4jSession) -> None:
        self._session = session

    async def clear_graph(self) -> dict[str, int]:
        """
        Delete every node and relationship from the current Neo4j database.

        Returns:
            Summary counts for deleted nodes and relationships so the caller
            can confirm the scale of the operation.
        """
        nodes_before = await self._count("MATCH (n) RETURN count(n) AS count")
        rels_before = await self._count(
            "MATCH ()-[r]-() RETURN count(r) AS count"
        )

        logger.warning(
            "Clearing Neo4j graph: deleting %s nodes and %s relationships",
            nodes_before,
            rels_before,
        )
        await self._session.run("MATCH (n) DETACH DELETE n")

        return {
            "nodes_deleted": nodes_before,
            "relationships_deleted": rels_before,
        }

    async def _count(self, query: str) -> int:
        result = await self._session.run(query)
        record = await result.single()
        return int(record["count"]) if record and "count" in record else 0
