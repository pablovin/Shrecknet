# Scene and Milestone → ShreckWorld Source references

This migration adds direct ShreckWorld Source identifiers to Neo4j Scene and
Milestone nodes. ShreckWorld Source records live in SQL, so the graph stores
their IDs as node properties; it does not create Neo4j Source nodes or new graph
relationships. The migration never removes or repoints existing
`DERIVED_FROM` relationships to legacy `EntityInstance` nodes. The
`derived_from.entity_instance_id` API field remains available.

## Stored properties

| Node property | Meaning |
| --- | --- |
| `source_id` | ShreckWorld narrative Source/library-item ID |
| `source_section_id` | Specific Story/Session section in the Source hierarchy |
| `source_markdown_asset_id` | Optional private per-section Markdown asset ID |
| `source_provenance_json` (Scene only) | Versioned passage evidence and Source reference |

All are optional. Existing graph-only records continue to work. The
`SceneRead.derived_from` object exposes optional `source_id`, `section_id`,
`markdown_asset_id`, `source_revision`, `start_paragraph`, `end_paragraph`,
`source_paragraphs_absolute`, and `provenance_status`. The required
`entity_instance_id` still comes from the graph relationship. The
`MilestoneRead.derived_from` object exposes optional `source_id`, `section_id`,
and `markdown_asset_id` alongside its required entity ID.

Document-level provenance identifies the exact Source and Story section even
when no paragraph span is recoverable. The paragraph indices are the
Architect's original one-based source indices; they are not guaranteed to match
revised Markdown. Recovered ranges are labeled `historical_unverified`. Do not
highlight a passage as exact until a source revision/text alignment check is
implemented.

## Explicit operator migration

Scene/Milestone Source references are **not** backfilled automatically on API
startup. Run the operator script from `shreckworld/`. Without `--apply`, it is
read-only and prints a preview. With `--apply`, it first runs the entity-to-
Source importer, waiting for any in-progress startup import to finish, then
reconciles Scene and Milestone properties:

```bash
cd shreckworld
python scripts/migrate_graph_scene_sources.py
python scripts/migrate_graph_scene_sources.py --apply
```

The apply run waits on its own interprocess lock. It resolves each legacy
`DERIVED_FROM` entity to the deterministic Narrative collection entry, accepts
only Source IDs that exist in ShreckWorld and belong to the same ontology, and
requires a unique Source for the entry. It replaces stale Scene/Milestone Source
properties with that verified mapping while preserving Scene paragraph evidence
and every legacy graph relationship. If the entity-to-Source importer reports
errors, the script skips graph writes and includes the reason in its report.
The operation is idempotent and can be run again explicitly after fixing missing
Sources or importer errors.

The final JSON report includes:

- `sources_added`, grouped by ontology, listing Source IDs and titles created by
  this invocation. Existing Sources are not listed as newly added.
- `scenes` and `milestones`, with one `linked`, `already_linked`, `would_link`,
  or `not_linked` row per graph record. Unlinked rows include a reason and the
  legacy source entity ID when available.
- Per-kind totals grouped globally and by ontology, plus the entity-to-Source
  importer summary.

The ShreckWorld API startup still runs the entity-to-Source importer according
to `SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP`. It no longer runs the
Scene/Milestone-reference migration. No Neo4j write occurs until the operator
supplies `--apply` for this migration; normal Scene/Milestone creation paths are
unchanged.

## Mapping and passage evidence

The migration reads active Narrative collection entries, their `source_ids`,
current ShreckWorld Source rows, and Markdown asset mappings from SQL. It
resolves collection entry IDs using the same deterministic key as the entity
import; names and slugs are not used as identity. It accepts a Source only when
the entry identifies exactly one existing same-ontology Source.

For Scenes, the migration preserves existing paragraph evidence. It can also
recover Architect paragraph ranges from:

- `/legacy-shrecknet-data/shrecknet_jobs.db` when jobs still exist;
- `/legacy-shrecknet-data/architect_provenance/analysis-job-*.json`;
- `/legacy-shrecknet-data/local_tests/arhictect/Analyses/*/pipeline_output.json`
  (original spelling of the Architect output directory).

Historical evidence matches only by exact ontology-instance ID, Scene ID, and
original source entity ID. Conflicting ranges are rejected. Missing or
ambiguous Sources are reported and left unlinked. If historical Architect
artifacts are unavailable, document-level references are still written and
paragraph fields remain empty. No `DERIVED_FROM`, `HAS_SCENE`, `CONTAINS`,
`RELATES_TO`, or narrative data changes.

The Source importer may run in a daemon startup thread. The Scene/Milestone
reference migration is separate and operator-triggered; inspect its JSON output
to verify coverage and unresolved records.

## Query scenes by new Source

```cypher
MATCH (scene:Scene)
WHERE scene.source_id = $source_id
  AND scene.source_section_id = $section_id
RETURN scene.id, scene.name, scene.source_provenance_json
ORDER BY scene.created_at
```

An additive Neo4j index on `(Scene.source_id, Scene.source_section_id)` is
created during Shrecknet graph startup. Existing graph readers continue to use
legacy entity `DERIVED_FROM` relationships unless they explicitly read the new
Source ID properties.

## Deployment and rollback

Deploy the ShreckWorld migration script with access to ShreckWorld SQL, Neo4j,
and the existing Shrecknet legacy databases/media. Back up ShreckWorld SQLite,
Neo4j, and the Shrecknet databases/media before applying it.

New properties are additive. Reverting this phase leaves the existing
`DERIVED_FROM` relationships intact, so existing graph readers continue
working. New Source ID properties may remain on graph nodes harmlessly.
