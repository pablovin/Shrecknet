# Scene → ShreckWorld Source provenance (compatibility phase)

This phase **adds** direct ShreckWorld Source identifiers to Neo4j Scenes.
It does **not** remove or repoint any existing
`(scene:Scene)-[:DERIVED_FROM]->(legacy:EntityInstance)` relationships. Those
legacy edges and the original `derived_from.entity_instance_id` API field stay
exactly as before. Character Agent, Atlas, Elder, Milestone, embedding, and
graph-retrieval systems will be migrated separately.

## Stored Scene properties

| Neo4j property | Meaning |
| --- | --- |
| `source_id` | ShreckWorld's narrative Source/library-item ID |
| `source_section_id` | Specific Story/Session section in the Source hierarchy |
| `source_markdown_asset_id` | Optional private per-section Markdown asset ID |
| `source_provenance_json` | Optional, versioned passage evidence (serialized `SceneDerivedFrom`) |

All are optional. Existing graph-only Scenes continue to work. The
`SceneRead.derived_from` object now also exposes optional
`source_id`, `section_id`, `markdown_asset_id`, `source_revision`,
`start_paragraph`, `end_paragraph`, `source_paragraphs_absolute`, and
`provenance_status`. The existing `entity_instance_id` remains required
and comes from the graph edge, not the JSON copy.

Document-level provenance identifies the exact new Source and Story section
even when no paragraph span is recoverable. Missing evidence yields no
paragraph IDs or range. The paragraph indices are **the Architect's original
one-based source indices**: they are not guaranteed to match revised Markdown,
so inferred matches are labeled `historical_unverified`. Do not highlight a
passage as exact until a source revision/text alignment check is implemented.

## New Architect runs

The Architect's segmentation already records paragraph ranges during analysis.
They now survive the frontend review schema and flow into `SceneCreate`.
During generation, Shrecknet reads the `graph_source_imports` and
`source_assets` mapping from its existing ShreckWorld database mount at
`/backup-sources/shreckworld/data/shreckworld.db` (read-only SQLite connection).
It resolves the Source and section by **all three** original identifiers:
ontology, ontology instance, legacy entity instance. The user-editable review
payload cannot supply an arbitrary Source ID in place of this mapping.

If the Source import has not yet completed, the Scene is still created with
its legacy graph origin and paragraph evidence. On a future startup, the
migration fills its Source IDs once the matching Source exists. The new
paragraph evidence is retained even when the document reference is missing.

`source_revision` is intentionally null if the reviewed legacy text was not
verified against that exact Source Markdown revision; paragraph positions alone
must not claim such a match.

## Automatic startup backfill

After ShreckWorld finishes its automatic graph Sources importer, the
`scene_source_provenance_import` migration runs. It is insert-only toward
missing metadata: it will not replace existing, conflicting Source IDs or
paragraph anchors and uses the same media-root, interprocess file locking
approach as the Sources importer.

- Read `graph_source_imports` and current Markdown asset mappings from
  ShreckWorld SQL.
- Read historical Architect analysis evidence from:
  - `/legacy-shrecknet-data/shrecknet_jobs.db` when jobs still exist;
  - `/legacy-shrecknet-data/architect_provenance/analysis-job-*.json`;
  - `/legacy-shrecknet-data/local_tests/arhictect/Analyses/*/pipeline_output.json`
    (original spelling of the Architect output directory).
- Match evidence **only** by exact ontology-instance ID, Scene ID, and
  original source entity ID. Conflicting ranges are rejected; no semantic/LLM
  guesses or generated paragraph numbers.
- Write `Scene.source_id`, `Scene.source_section_id`, optional
  `Scene.source_markdown_asset_id` and `source_provenance_json`. No
  `DERIVED_FROM`, `HAS_SCENE`, Milestone, `RELATES_TO`, or narrative
  data changes.
- On restart, keep already populated, nonconflicting fields unchanged. Missing
  Sources are skipped without inventing IDs. If logs/artifacts are unavailable,
  preserve document-level references and leave paragraphs empty.

The Shrecknet startup job-cleanup path now writes a reduced copy of completed
Architect analysis Scene evidence to its durable `architect_provenance`
directory **before** deleting background jobs. If preservation fails, the
cleanup keeps those jobs to avoid silently destroying provenance. Old job
records deleted by previous Shrecknet versions cannot be resurrected; surviving
local analysis artifacts may still contain those records.

The ShreckWorld importer runs in a daemon startup thread after schema setup.
The Scene backfill runs **after** that attempt. The web API is not blocked;
check ShreckWorld logs for `Scene source provenance startup reconciliation`.
The migration can be safely retried on another service start.

## Query derived Scenes by new Source

```cypher
MATCH (scene:Scene)
WHERE scene.source_id = $source_id
  AND scene.source_section_id = $section_id
RETURN scene.id, scene.name, scene.source_provenance_json
ORDER BY scene.created_at
```

An additive Neo4j index on `(Scene.source_id, Scene.source_section_id)` is
created during Shrecknet graph startup. As of this phase, **existing** graph
readers continue to use the original graph `DERIVED_FROM` relationships.
Migrating those readers and retiring the old edges are explicit future tasks.

## Deployment and rollback

This PR is layered on the default-on Sources importer (PR #468). Deploy it
after the Sources import and with access to the existing ShreckWorld database
and Shrecknet legacy databases. Back up ShreckWorld SQLite, Neo4j, and the
Shrecknet databases/media before the first deploy.

New fields and graph indexes are additive. Reverting this phase leaves the
existing `DERIVED_FROM` relationships intact, so existing graph readers
continue working. The new Source properties may remain on Scenes harmlessly.
