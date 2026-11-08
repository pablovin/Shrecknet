"""Project retrieved Atlas property snapshots using authoritative ontology labels."""
import json
import math
from app.repositories.ontology_repository import OntologyRepository


async def fields_for(row, ontology_id, session):
    properties = row.properties or {}
    definition = properties.get('entity_definition_id')
    values = properties.get('properties')
    if not definition or not values:
        return []
    if isinstance(values, str):
        if len(values) > 64000:
            return []
        try:
            values = json.loads(values)
        except ValueError:
            return []
    if not isinstance(values, dict):
        return []
    definitions = await OntologyRepository(session).list_properties(ontology_id, definition)
    fields = []
    for prop in definitions:
        if str(prop.id) not in values:
            continue
        value = values[str(prop.id)]
        if (value is not None and type(value) not in {str, int, float, bool}) or len(str(value)) > 500:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if prop.name and len(prop.name) <= 80:
            fields.append({'label': prop.name, 'value': value})
        if len(fields) == 100:
            break
    return fields
