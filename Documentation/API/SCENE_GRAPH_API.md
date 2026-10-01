# Scene Graph API

`GET /ontologies/{ontology_id}/scenes/{scene_id}/graph` is the optimized read
contract for rendering one canonical scene without issuing follow-up reads for
milestones, referenced entities, or visible Character Agent memories.

The existing `GET /ontology-instances/{instance_id}/scenes/{scene_id}` endpoint
is unchanged and remains available for compatibility.

## Authorization

The request requires an authenticated user. Admins, world builders, and writers
may read a world graph. A player may read it only when assigned to an entity in
the requested ontology. Requests outside that scope return `403`; an unknown
ontology or a scene not belonging to the requested ontology returns `404`.

Character Agent perspective visibility is checked inside the projection. Admins
receive perspectives owned by public and private agents. Other authorized users
receive perspectives owned by public agents only. The route never returns a
private perspective merely because its scene is visible.

## Response

The response has these top-level fields:

- `scene`: the existing canonical `SceneRead` contract, including milestones and
  each scene/milestone `local_order` link.
- `source_page`: `{id, display_name, avatar_url}` for the owning ontology
  instance.
- `related_entities`: one entry per entity referenced by the scene or any of its
  milestones. Entries contain `id`, alias-derived `canonical_content_slug`,
  `display_name`, `avatar_url`, and every normalized `relation_labels` value.
  `derived_from` is included as a relation label for provenance edges.
- `perspectives`: visibility-authorized Character Agent perspectives. Every item
  contains its agent display reference plus its `emotions`, `beliefs`, and
  `impacts` aggregates.
- `previous_scene` and `next_scene`: nullable summaries linked by the scene's
  `PRECEDED_BY` and `FOLLOWED_BY` timeline edges.

Entity display metadata is fetched in one batch after relation IDs are
deduplicated. This makes repeated scene and milestone references stable in the
response and avoids per-entity graph reads.

Example shape:

```json
{
  "source_page": {"id": "page-7", "display_name": "Chapter Seven", "avatar_url": null},
  "related_entities": [
    {"id": "entity-ada", "canonical_content_slug": "captain-ada", "display_name": "Captain Ada", "avatar_url": null, "relation_labels": ["derived_from", "participant"]}
  ],
  "previous_scene": null,
  "next_scene": {"id": "scene-9", "name": "The Crossing", "description": "...", "source_page": {"id": "page-8", "display_name": "Chapter Eight", "avatar_url": null}}
}
```
