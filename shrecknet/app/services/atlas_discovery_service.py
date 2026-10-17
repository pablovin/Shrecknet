"""Read canonical provenance in fixed graph round trips, hydrating only a bounded page."""
from neo4j import AsyncSession

from app.schemas.ontology_instance import SceneGraphSourcePageReference
from app.schemas.atlas import AtlasScenePage, AtlasSceneRow, AtlasSourceCard, AtlasSourcePage


# Match mixed historical string/integer world IDs without wrapping indexed properties.
_SCENES = """
MATCH (scene:Scene)
WHERE scene.ontology_id IN [$ontology_id, toString($ontology_id)]
MATCH (owner:OntologyInstance)-[:HAS_SCENE]->(scene)
WHERE owner.ontology_id IN [$ontology_id, toString($ontology_id)]
"""
_FILTERS = """
AND ($scene_query IS NULL OR toLower(coalesce(scene.name, '')) CONTAINS $scene_query
     OR toLower(coalesce(scene.description, '')) CONTAINS $scene_query)
AND ($related_entity_id IS NULL OR EXISTS {
    MATCH (scene)-[membership:DERIVED_FROM|RELATES_TO]->(target:EntityInstance)
    WHERE target.entity_instance_id = $related_entity_id
      AND (NOT $related_only OR type(membership) = 'RELATES_TO')
} OR EXISTS {
    MATCH (scene)-[:CONTAINS]->(:Milestone)-[membership:DERIVED_FROM|RELATES_TO]->(target:EntityInstance)
    WHERE target.entity_instance_id = $related_entity_id
      AND (NOT $related_only OR type(membership) = 'RELATES_TO')
})
AND ($source_entity_id IS NULL OR EXISTS {
    MATCH (scene)-[:DERIVED_FROM]->(source:EntityInstance)
    WHERE source.entity_instance_id = $source_entity_id
})
AND (NOT $has_perspectives OR EXISTS {
    MATCH (agent:CharacterAgent)-[:HAS_PERSPECTIVE]->(:ScenePerspective)-[:PROJECTS_ON]->(scene)
    WHERE $include_private OR coalesce(agent.visibility, 'private') = 'public'
})
"""


class AtlasDiscoveryService:
    def __init__(self, graph_session: AsyncSession):
        self.graph_session = graph_session

    async def scene_source(self, *, ontology_id: int, scene_id: str) -> SceneGraphSourcePageReference:
        result = await self.graph_session.run(
            _SCENES + """
            AND scene.id = $scene_id
            MATCH (scene)-[:DERIVED_FROM]->(anchor:EntityInstance)<-[:HAS_ENTITY]-(source:OntologyInstance)
            WHERE source.ontology_id IN [$ontology_id, toString($ontology_id)]
            RETURN source.instance_id AS id, source.name AS display_name, anchor.node_avatar_url AS avatar_url
            ORDER BY anchor.entity_instance_id ASC, id ASC LIMIT 1
            """, ontology_id=ontology_id, scene_id=scene_id,
        )
        row = await result.single()
        if row is None:
            raise ValueError("Scene source not found")
        return SceneGraphSourcePageReference.model_validate(dict(row))

    async def sources(self, *, ontology_id: int, skip: int = 0, limit: int = 10) -> AtlasSourcePage:
        result = await self.graph_session.run(
            _SCENES + """
            MATCH (scene)-[:DERIVED_FROM]->(anchor:EntityInstance)<-[:HAS_ENTITY]-(source:OntologyInstance)
            WHERE source.ontology_id IN [$ontology_id, toString($ontology_id)]
            WITH source, count(DISTINCT scene) AS scene_count,
                 max(coalesce(toString(scene.updated_at), toString(scene.created_at), '')) AS activity,
                 min(anchor.node_avatar_url) AS avatar_url
            RETURN source.instance_id AS id, source.name AS display_name,
                   avatar_url, scene_count, activity AS latest_activity
            ORDER BY latest_activity DESC, id ASC
            SKIP $skip LIMIT $fetch_limit
            """, ontology_id=ontology_id, skip=skip, fetch_limit=limit + 1,
        )
        rows = await result.data()
        return AtlasSourcePage(
            results=[AtlasSourceCard.model_validate(row) for row in rows[:limit]],
            skip=skip, limit=limit, has_more=len(rows) > limit,
        )

    async def scenes(self, *, ontology_id: int, query: str | None = None,
                     related_entity_id: str | None = None, source_entity_id: str | None = None,
                     has_perspectives: bool = False, include_private: bool = False, related_only: bool = False,
                     skip: int = 0, limit: int = 30) -> AtlasScenePage:
        arguments = dict(ontology_id=ontology_id, scene_query=(query or '').strip().lower() or None,
                         related_entity_id=related_entity_id, source_entity_id=source_entity_id,
                         has_perspectives=has_perspectives, include_private=include_private, related_only=related_only)
        count = await self.graph_session.run(
            _SCENES + _FILTERS + ' RETURN count(DISTINCT scene) AS total', **arguments,
        )
        total = int((await count.single())["total"])
        if not total or skip >= total:
            return AtlasScenePage(results=[], total=total, skip=skip, limit=limit, related_only=related_only)
        result = await self.graph_session.run(
            _SCENES + _FILTERS + """
            WITH DISTINCT scene, owner
            ORDER BY coalesce(toString(scene.updated_at), toString(scene.created_at), '') DESC, scene.id ASC
            SKIP $skip LIMIT $limit
            CALL {
                WITH scene
                OPTIONAL MATCH (scene)-[:DERIVED_FROM]->(anchor:EntityInstance)
                OPTIONAL MATCH (anchor)<-[:HAS_ENTITY]-(source:OntologyInstance)
                WHERE source.ontology_id IN [$ontology_id, toString($ontology_id)]
                WITH anchor, source ORDER BY anchor.entity_instance_id ASC, source.instance_id ASC
                RETURN head(collect(anchor.entity_instance_id)) AS source_entity_id,
                       head(collect(CASE WHEN source IS NULL THEN null ELSE {
                         id: source.instance_id, display_name: source.name, avatar_url: anchor.node_avatar_url
                       } END)) AS source_page
            }
            CALL {
                WITH scene
                OPTIONAL MATCH (scene)-[:CONTAINS]->(milestone:Milestone)
                RETURN count(DISTINCT milestone) AS milestone_count
            }
            CALL {
                WITH scene
                OPTIONAL MATCH (agent:CharacterAgent)-[:HAS_PERSPECTIVE]->(perspective:ScenePerspective)-[:PROJECTS_ON]->(scene)
                WHERE $include_private OR coalesce(agent.visibility, 'private') = 'public'
                RETURN count(DISTINCT perspective) AS perspective_count
            }
            RETURN scene.id AS id, scene.name AS name, scene.description AS description,
                   owner.instance_id AS instance_id, owner.name AS instance_name,
                   source_entity_id, source_page, milestone_count, perspective_count,
                   head([(scene)-[:FOLLOWED_BY]->(next:Scene) | next.id]) AS next_id,
                   head([(scene)-[:PRECEDED_BY]->(previous:Scene) | previous.id]) AS previous_id
            """, **arguments, skip=skip, limit=limit,
        )
        rows = []
        for row in await result.data():
            rows.append(AtlasSceneRow.model_validate({
                "scene": {"id": row["id"], "name": row["name"], "description": row.get("description"),
                          "derived_from": {"entity_instance_id": row["source_entity_id"]} if row.get("source_entity_id") else None,
                          "local_order": {"followed_by_scene_id": row.get("next_id"), "preceded_by_scene_id": row.get("previous_id")},
                          "milestone_count": row["milestone_count"]},
                "instance": {"instance_id": row["instance_id"], "name": row["instance_name"]},
                "source_page": row.get("source_page"), "perspective_count": row["perspective_count"],
            }))
        return AtlasScenePage(results=rows, total=total, skip=skip, limit=limit, related_only=related_only)
