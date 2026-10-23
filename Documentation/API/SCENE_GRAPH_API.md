# Scene Graph API

`GET /ontologies/{ontology_id}/scenes/{scene_id}/graph` is the optimized read
contract for rendering one canonical scene without issuing follow-up reads for
milestones, referenced entities, or visible Character Agent memories.

The existing `GET /ontology-instances/{instance_id}/scenes/{scene_id}` endpoint
is unchanged and remains available for compatibility.

## Authorization

Every authenticated user, including a player without entity assignments, may read
any existing world graph. An unknown ontology or a scene not belonging to the
requested ontology returns `404`. Authentication is still required; authoring
permissions are unchanged. This also applies to discovery, sources, Folio reads,
World Web, and Elder cards through the shared world-read boundary.

Deploy the updated Shrecknet service to enable this policy; no data migration is
required. Rolling back restores the previous assignment requirement for players.

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
  contains `source_type`, `perspective`, its agent display reference, and its
  `emotions`, `beliefs`, and `impacts` aggregates.
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

## Atlas discovery

The following authenticated reads share the graph-detail endpoint's world access
policy. Unauthorized worlds return `403`, unknown worlds return `404`, and invalid
pagination/filter arguments return `422`. No route bypasses access checks through
an empty cache or an inferred source identity.

### `GET /ontologies/{ontology_id}/scene-sources`

`skip` defaults to 0 and `limit` to 10 (1–100). Returns
`{results, skip, limit, has_more}`. Each result is
`{id, display_name, avatar_url, scene_count, latest_activity}`. IDs address source
**pages**, not anchor entities or the containers that own a scene.

Sources follow `(scene)-[:DERIVED_FROM]->(entity)<-[:HAS_ENTITY]-(source_page)`
within the authorized world. Multiple source entities in one page produce one
card. Missing/unresolvable provenance does not become the owning container.
Cards sort by the latest source-scene update/creation timestamp descending, then
source-page ID ascending; this is activity, not fictional chronology. Artwork is
selected deterministically from non-null source-anchor artwork. Empty worlds
return an empty page and `has_more: false`.

One graph projection aggregates sources, returns at most `limit + 1` lightweight
rows for lookahead, and exposes only `limit` cards. It does not fetch full scene,
entity, narrative, or perspective bodies. All sources are eligible, including those
whose scenes fall beyond the former first-100 browser-side cutoff. Increment
`skip` by the returned result count. Pagination is live rather than a snapshot;
concurrent activity can move a page between requests, so clients should deduplicate
by source ID.

### `GET /ontologies/{ontology_id}/scenes`

Parameters: `query` (optional, up to 500 characters), `related_entity_id`,
`source_entity_id` (optional, up to 250 characters each), `has_perspectives`
(default false), `related_only` (default false), `skip` (default 0), and `limit`
(default 30, 1–100). `related_only=true` requires `related_entity_id` and matches
only `RELATES_TO` edges on the scene or its milestones; it excludes provenance
`DERIVED_FROM` edges. The default related filter continues to match either edge.

Returns `{results, total, skip, limit}`. Each result has:

- `scene`: `id`, `name`, nullable `description` and `derived_from`, explicit
  `local_order`, and `milestone_count`. This is an index projection, not `SceneRead`;
  fetch graph detail for milestone bodies and other full scene data.
- `instance`: the owning container's `instance_id` and `name`.
- `source_page`: nullable `{id, display_name, avatar_url}` resolved from the
  canonical provenance anchor. It is distinct from `instance`.
- `perspective_count`: visible perspectives only. Private Character Agent
  perspectives contribute only for admins, including when filtering by
  `has_perspectives`.

Text search matches scene names/descriptions case-insensitively before
pagination. Related-entity filtering matches scene or milestone provenance and
related edges. Exact `source_entity_id` filtering matches only a scene's own
`DERIVED_FROM` edge; it is the efficient Scene Track path. Sort order is latest
update/creation descending with scene ID as tie-breaker, never fictional sequence.

The service performs one indexed world-scoped count and one bounded card
projection, with pagination before metadata/count hydration. Empty results skip
the projection. There is no per-record graph or SQL request and no browser
full-world scan. Count and rows are live reads, not a transactional snapshot.

### `GET /ontologies/{ontology_id}/scenes/{scene_id}/source`

Returns `{id, display_name, avatar_url}` for the scene's canonical provenance
page in one graph projection. Missing scene/source or out-of-world provenance
returns `404`, after world authorization. This lightweight evidence-link read
avoids downloading the full scene graph. It does not change the existing graph
endpoint's historical owning-instance `source_page` semantics.

### `GET /ontologies/{ontology_id}/elders`

`skip` defaults to 0 and `limit` to 100 (1–100). Returns an array of compact
`{id, name, description, avatar_url}` cards for active Elder agents assigned to
this authorized world. Orders by agent creation descending, then ID ascending;
clients choose the first returned Elder by default. Continue when a page is full.

The Agent repository uses one narrow SQL projection without unrelated agents,
writing configuration, or ontology relationship hydration. Authentication and
world authorization precede listing. Agent chat/query authorization remains
service-owned. Existing `DELETE /jobs/elder/chats/{chat_id}` deletes only the
caller's chat and messages, returning `204`; inaccessible/missing chats return
`404`.

## Living Folio page reads

The following authenticated reads use the same world graph-access policy above.
Their `page_id` is an ontology-instance page ID, never an entity-instance ID.
Unavailable pages return `404` after world authorization.

### `GET /ontologies/{ontology_id}/pages/{page_id}/folio`

Returns the page identity and its bounded entity/narrative content using the
existing `OntologyInstanceRead` fields, plus
`folio_projection: {"scenes": "omitted", "relationships": "omitted"}`.
`scenes` and each entity's `relationships` are empty in this projection; the
marker distinguishes omitted data from an actually empty collection. Fetch
relationships from the dedicated endpoint and scenes from the scene index.

### `GET /ontologies/{ontology_id}/pages/{page_id}/authors`

Returns distinct public `{id, type, display_name, avatar_url}` author summaries
for the page. Missing author profiles are not exposed as identity data.

### `GET /ontologies/{ontology_id}/pages/{page_id}/relationships`

Parameters: `entity_id` (optional focus entity ID), `query` (optional, up to 500
characters), `group` (optional relationship group key), `skip` (default 0), and
`limit` (default 30, 1–100). Returns `{results, groups, total, skip, limit}`.
Rows preserve relationship direction and focus entity, and their `target.page_id`
identifies the target's owning page separately from `target.entity_id`.

These endpoints are additive. They do not change existing full instance reads.
The Folio detail projection does not include scene bodies or relationship
collections; relationship reads return only the requested page and never a
full-world graph.

## Discovery operations and compatibility

Atlas discovery is additive. Existing instance list, legacy scene summary, scene
detail, graph-detail, and Elder endpoints keep their contracts. No new store,
background process, canon write, or cache of private content is introduced.
`AtlasDiscoveryService` owns graph projections; the Agent service/repository own
Elder listing; routers own authentication and error mapping. Public Python methods
and models are documented in
[OntologiesAPI](../../python_sdk/docs/reference/ontologies.md#atlas-discovery).

Deploy the API before the consuming ShreckOS release. Normal API startup runs the
idempotent temporal-graph setup, now including the single-property
`Scene.ontology_id` index (`scene_ontology_idx`), alongside existing scene ID and
instance indexes. No data backfill is required; historical integer/string world
IDs are matched without wrapping the indexed property. Rollback can leave this
non-destructive index in place. Older frontend versions continue to work; the new
source shelf requires the new discovery endpoints and presents a retryable error
rather than silently scanning all records against an older API.

Performance is bounded by result size and fixed request counts, but aggregation,
filtering, and count still depend on world size. Representative production p95
latency and query-plan measurements remain rollout checks; local timings alone
are not a service-level guarantee.


## Compact World Web

`GET /ontologies/{ontology_id}/world-web` uses the existing authenticated Atlas world-read policy (403 denied, 404 missing). Returns `{ontology_id, nodes, edges, truncated}`. Nodes contain `id`, `kind` (`source`, `entity`, `scene`, `milestone`), `name`, and owning `page_id`; edges contain `source`, `target`, and `label`. IDs are namespaced graph identities. Scene/milestone page links target their owning container. No narrative, embedding or private perspective fields are read.

The service makes at most two Neo4j projections: world-owned entity/scene/milestone metadata and edges between admitted nodes. Edge targets must belong to that same admitted world set. Only DERIVED_FROM, CONTAINS, RELATES_TO and FOLLOWED_BY are included; reverse timeline duplicates are omitted. Responses stop at 20,000 nodes and 60,000 edges with one-row lookahead and `truncated=true` when either cap is exceeded. No global response cache or persistence migration is introduced. Startup creates the additive `ontology_instance_world_idx` on `OntologyInstance.ontology_id`; existing scene indexes are reused. Wait for the index to become ONLINE before load testing. Rollback can leave this non-destructive index in place. Profile actual production query plans and network latency before assigning a latency SLO.

Python SDK: `await client.ontologies.world_web(ontology_id)` returns `AtlasWorldWeb`. Deploy backend before clients that require this additive route; rollback leaves existing scene contracts unchanged.


### Admin partial Folio updates

`PATCH /ontologies/{ontology_id}/pages/{page_id}/folio` requires an authenticated
administrator and world graph access. World builders cannot use this endpoint;
legacy PUT permissions are unchanged. The page must belong to the world and the
selected entity must belong to that page, including on pages with multiple facets.

```json
{
  "expected_updated_at": "2026-10-01T00:00:00Z",
  "entity_instance_id": "entity-1",
  "name": "Revised title",
  "text": "Revised source text"
}
```

Required fields are `expected_updated_at` (the GET Folio `updated_at` timestamp)
and `entity_instance_id`. Supply one or more of `name`, `text`,
`autogenerated_text`, `node_avatar_url`, `properties`, or `relationships`.
Unknown fields are rejected. Name must be nonblank and at most 255 characters.
Only artwork and generated text may be explicitly null. Omitted fields stay
unchanged. Properties are replacement arrays of `{definition_id, value}`;
relationships replace the selected entity's outgoing collection using the existing
`{definition_id, target_entity_instance_id, data}` or `target_alias` creation
contract. Reciprocal edges, target world/type validation, and edge metadata follow
the existing ontology-instance service rules. Supplied unknown definition IDs are
rejected, not silently removed.

Authorship, creation metadata, sibling facets, scenes and milestones remain intact.
Linked text is reset only for the narrative actually changed; normal link and
embedding tasks are queued through the existing update pipeline. The page and
selected entity modification timestamps advance. The page revision is checked
again after acquiring the transaction lock; a stale revision returns 409 without
writes. A mismatched page/world or entity is rejected (400); authentication and
role failures return 401/403 and invalid payloads return 422. Other service failures
remain errors; clients should preserve their drafts.

The response is the same `AtlasFolioDetailRead` projection as GET, without expanded
scenes or relationship arrays. Inline saves avoid those expansions on reads and
skip relationship processing when that collection was not supplied. This is an
additive endpoint with no schema migration. Deploy it before the inline-editing
frontend; roll back the frontend first. Existing PUT callers keep their contract.
Validate live Neo4j concurrent edits and service latency during deployment; mocked
transaction tests do not replace that operational check.
