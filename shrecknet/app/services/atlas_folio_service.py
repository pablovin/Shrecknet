"""Bounded page exploration; IDs retain their canonical page/node meaning."""
from sqlalchemy import select

from app.models.agent import Agent
from app.models.user import User
from app.models.ontology import OntologyRelationship
from app.schemas.atlas import AtlasAuthor, AtlasFolioDetailRead, AtlasRelationshipPage, AtlasFolioPatch
from app.schemas.ontology_instance import OntologyInstanceEntityCreate, OntologyInstanceUpdate
from app.services.ontology_instance_service import OntologyInstanceService, FolioEditConflict


_RELATIONSHIPS = """
MATCH (page:OntologyInstance {instance_id: $page_id})-[:HAS_ENTITY]->(focus:EntityInstance)
WHERE page.ontology_id IN [$ontology_id, toString($ontology_id)]
  AND ($entity_id IS NULL OR focus.entity_instance_id = $entity_id)
MATCH (focus)-[edge:RELATES_TO]-(other:EntityInstance)<-[:HAS_ENTITY]-(owner:OntologyInstance)
WHERE owner.ontology_id IN [$ontology_id, toString($ontology_id)]
  AND other.ontology_id IN [$ontology_id, toString($ontology_id)]
  AND focus.ontology_id IN [$ontology_id, toString($ontology_id)]
WITH DISTINCT focus, edge, other, owner,
     toInteger(edge.relationship_definition_id) AS definition_id,
     CASE WHEN startNode(edge) = focus THEN 'outgoing' ELSE 'incoming' END AS direction
WHERE definition_id IS NOT NULL
  AND ($search_text IS NULL OR toLower(coalesce(other.alias, owner.name, '')) CONTAINS $search_text)
"""


class AtlasFolioService:
    def __init__(self, instances: OntologyInstanceService):
        self.instances = instances
        self.graph = instances.graph_session
        self.sql = instances.sql_session

    async def assert_page(self, ontology_id: int, page_id: str) -> None:
        result = await self.graph.run("""
            MATCH (page:OntologyInstance {instance_id: $page_id})
            WHERE page.ontology_id IN [$ontology_id, toString($ontology_id)]
            RETURN page.instance_id AS id
            """, ontology_id=ontology_id, page_id=page_id)
        if await result.single() is None:
            raise ValueError("Page unavailable")

    async def detail(self, ontology_id: int, page_id: str) -> AtlasFolioDetailRead:
        await self.assert_page(ontology_id, page_id)
        detail = await self.instances.get_instance(page_id, include_scenes=False, include_relationships=False)
        return AtlasFolioDetailRead.model_validate({
            **detail.model_dump(),
            "folio_projection": {"scenes": "omitted", "relationships": "omitted"},
        })

    async def patch(self, ontology_id: int, page_id: str, payload: AtlasFolioPatch) -> AtlasFolioDetailRead:
        await self.assert_page(ontology_id, page_id)
        current = await self.instances.get_instance(page_id, include_scenes=False, include_relationships=False)
        if current.updated_at != payload.expected_updated_at:
            raise FolioEditConflict("This page changed. Reload before saving again.")
        entity = next((row for row in current.entities if row.entity_instance_id == payload.entity_instance_id), None)
        if entity is None:
            raise ValueError("Entity does not belong to this page")
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at", "entity_instance_id", "name"})
        original = entity.model_dump(exclude={"relationships"})
        merged = OntologyInstanceEntityCreate.model_validate({**original, **changes})
        updated = await self.instances.update_instance(page_id, OntologyInstanceUpdate(
            name=payload.name, entities=[merged],
        ), expected_updated_at=payload.expected_updated_at, edit_fields=set(changes))
        # Return the same bounded projection as GET; no scenes or relationship array.
        result = updated.model_dump()
        result["entities"] = [{**row, "relationships": []} for row in result["entities"]]
        return AtlasFolioDetailRead.model_validate({**result,
            "folio_projection": {"scenes": "omitted", "relationships": "omitted"}})

    async def authors(self, ontology_id: int, page_id: str) -> list[AtlasAuthor]:
        await self.assert_page(ontology_id, page_id)
        result = await self.graph.run("""
            MATCH (:OntologyInstance {instance_id: $page_id})-[:HAS_ENTITY]->(entity:EntityInstance)
            RETURN DISTINCT entity.author_type AS type, entity.author_id AS id
            """, page_id=page_id)
        rows = await result.data()
        human_ids = [int(row['id']) for row in rows if row.get('type') == 'human' and str(row.get('id', '')).isdigit()]
        agent_ids = [str(row['id']) for row in rows if row.get('type') == 'agent' and row.get('id')]
        authors = []
        if human_ids:
            result = await self.sql.execute(select(User.id, User.full_name, User.username, User.avatar_url).where(User.id.in_(human_ids)))
            authors.extend(AtlasAuthor(id=str(row.id), type='human', display_name=row.full_name or row.username, avatar_url=row.avatar_url) for row in result.all())
        if agent_ids:
            result = await self.sql.execute(select(Agent.id, Agent.name, Agent.avatar_url).where(Agent.id.in_(agent_ids)))
            authors.extend(AtlasAuthor(id=row.id, type='agent', display_name=row.name, avatar_url=row.avatar_url) for row in result.all())
        return authors

    async def relationships(self, *, ontology_id: int, page_id: str, entity_id: str | None = None,
                            query: str | None = None, group: str | None = None,
                            skip: int = 0, limit: int = 30) -> AtlasRelationshipPage:
        await self.assert_page(ontology_id, page_id)
        arguments = dict(ontology_id=ontology_id, page_id=page_id, entity_id=entity_id, search_text=(query or '').strip().lower() or None)
        result = await self.graph.run(_RELATIONSHIPS + """
            RETURN definition_id, direction, count(*) AS total
            ORDER BY definition_id, direction
            """, **arguments)
        groups = [dict(key=f"{row['definition_id']}:{row['direction']}", definition_id=row['definition_id'], direction=row['direction'], total=row['total']) for row in await result.data()]
        definition_ids = [row['definition_id'] for row in groups]
        if definition_ids:
            names = await self.sql.execute(select(OntologyRelationship.id, OntologyRelationship.name).where(OntologyRelationship.id.in_(definition_ids)))
            labels = dict(names.all())
            for row in groups:
                row['label'] = labels.get(row['definition_id'])
        total = sum(row['total'] for row in groups if group is None or row['key'] == group)
        if not total or skip >= total:
            return AtlasRelationshipPage(results=[], groups=groups, total=total, skip=skip, limit=limit)
        result = await self.graph.run(_RELATIONSHIPS + """
            AND ($group IS NULL OR toString(definition_id) + ':' + direction = $group)
            WITH focus, edge, other, owner, definition_id, direction
            ORDER BY toLower(coalesce(other.alias, owner.name, '')), other.entity_instance_id, elementId(edge), focus.entity_instance_id
            SKIP $skip LIMIT $limit
            RETURN coalesce(edge.relationship_instance_id, elementId(edge)) + ':' + focus.entity_instance_id AS id,
                   definition_id, direction, focus.entity_instance_id AS focus_entity_id,
                   {entity_id: other.entity_instance_id, page_id: owner.instance_id,
                    definition_id: toInteger(other.entity_definition_id),
                    title: coalesce(other.alias, owner.name, other.entity_instance_id),
                    avatar_url: other.node_avatar_url} AS target
            """, **arguments, group=group, skip=skip, limit=limit)
        return AtlasRelationshipPage(results=await result.data(), groups=groups, total=total, skip=skip, limit=limit)
