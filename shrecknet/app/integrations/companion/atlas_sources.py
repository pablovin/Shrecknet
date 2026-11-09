"""Canonical Atlas identity and revision reads for validation and source media."""


def atlas_revision(properties, label):
    """Match the timestamp used by the owning graph write model."""
    if label == 'EntityInstance':
        return properties.get('last_updated_date') or properties.get('updated_at')
    return properties.get('updated_at')


async def read_atlas_source(graph, reference, ontology_id):
    """Read one typed source; parent scene/instance IDs never identify a source."""
    label = reference.get('node_label')
    if label not in {'EntityInstance', 'Scene', 'Milestone'}:
        raise ValueError('source_changed')
    identifier = "coalesce(n['entity_instance_id'], n['id'])" if label == 'EntityInstance' else "n['id']"
    revision = "coalesce(n['last_updated_date'], n['updated_at'])" if label == 'EntityInstance' else "n['updated_at']"
    # Only the allowlisted label and fixed expressions are interpolated. User IDs
    # remain parameters; dynamic property access avoids optional-key warnings.
    result = await graph.run(
        f"MATCH (n:{label}) WHERE n.ontology_id=$ontology AND {identifier}=$id "
        f"RETURN n['node_avatar_url'] AS image, {revision} AS revision LIMIT 2",
        ontology=ontology_id, id=reference['node_id'])
    rows = await result.data()
    if len(rows) > 1:
        raise ValueError('source_changed')
    return rows[0] if rows else None
