# Companion rich response implementation and acceptance plan

Status: implemented locally with `companion_rich_response_enabled=true` by
default for new/missing settings on `evidence_v2`; saved false values are preserved. Live provider quality/latency, human image-grounding and
real Foundry v14 sheet acceptance remain deployment gates. This document records
the delivered design; the [Companion guide](Companion.md#verified-rich-responses),
[frontend contract](Frontend%20API.md#rich-response-v1), and
[ShreckWorld media contract](../../API/SHRECKWORLD_READER_API.md#document-media)
are authoritative for operation.

## Scope and relationship to the query redesign

Character Companion (`/users/me/character-companion`) shares one versioned rich
response contract between ShreckOS and Foundry's workspace/compact sidecar.
Narrative, entity/scene cards, source images/galleries, structured property tables
and exact book excerpt cards are delivered. The separate personal-companion
orchestrator and notification settings are unchanged. Dice rolls, timelines,
stat sheets, inventories, relationship diagrams, generated artwork and writes or
broadcasts to Foundry remain outside this release.

The [query pipeline redesign](Query%20Pipeline%20Plan.md) remains the orchestrator:
explicit/saved scope fast paths → routing → optional ontology update/replanning →
parallel retrieval → assessment and bounded recovery → private synthesis → private
composition → combined verification/repair → source/asset revalidation → atomic
final message/event/rapport outbox. Durable status/commentary remains independent
of factual final publication. Factual tokens and blocks never stream before
verification. Failed optional media or rejected presentation cannot erase a
verified narrative. Existing source failures cannot become claims of completeness.

## Delivered ownership and behavior

| Owner | Responsibility |
| --- | --- |
| `app/jobs/companion/query_pipeline.py` | Insert optional media/composition into v2, verify block claims with narrative, share the existing single repair allowance, preserve source checks and leased finalization |
| `app/jobs/companion/rich_response.py` | Deterministic layout, exact field/quote projection, verifier segments, pruning and complete text fallback |
| `app/schemas/companion_rich_response.py` | Bounded discriminated blocks, navigation targets and asset-delivery descriptors |
| `app/integrations/companion/references.py` | Authoritative normalization of admitted source references and stable presentation IDs |
| `app/integrations/companion/atlas_properties.py` | Reuse `OntologyRepository` to label retrieved property snapshots without copying persistence logic |
| `app/integrations/companion/media.py` | Source-bound optional discovery and final Atlas asset checks; independent failures preserve successful siblings |
| `app/services/companion_asset_service.py` | Owner-only saved Atlas image reads and current source/scope/file checks |
| `CompanionRepository.message_records` | Project saved envelopes into private final-message history without a new table/column |
| ShreckWorld `app/services/document_media.py` | Rebuildable revision-bound PDF thumbnails/picture crops beside the existing source, independent of embedding/index activation |
| ShreckOS `shreckos/src/features/companion/` | Typed decoding, inert block rendering, citations/navigation, authenticated blobs, accessible lightbox and stable table sorting |
| Foundry `scripts/apps/companion-rich-response.js` | Inert view models for escaped templates, scoped source actions, protected image loading, local document image checks and expansion |
| Python SDK | Generated boundary models, raw result compatibility, typed `turn.rich_response`, presentation config helpers, protected Atlas bytes and runnable example |

No new model role, framework or dependency is introduced. Deterministic selection
uses draft-supported sources; a later model selector is optional and would require
its own measured justification. The existing evidence model verifies all public
facts, including added captions/description/quote attributions and table values.
Individual optional block rejections prune those blocks without triggering a
second repair loop. Entity descriptions reuse bounded factual draft text. Tables
project scalar/null properties whose source labels occur in that source's draft
narrative. No prose-to-statistics inference is implemented.

Atlas uses a node's own `node_avatar_url`, never category/ontology fallback art.
Only configured local storage maps to protected raster delivery; remote Atlas
artwork stays unavailable in this release. Milestone evidence remains narrative
because it has no standalone rich reader target. Foundry context v2 carries only
an `image_available` flag; actual images resolve locally from admitted UUIDs with
current table, revision and visibility checks. ShreckOS provides an explanatory
local-image fallback. Version-one snapshot hashes remain compatible.

ShreckWorld's current parser output does not provide reusable picture files, so
media extraction extends the owning source service with atomic page manifests
instead of rebuilding text embeddings. PDF page pictures are cropped from source
bounds and labeled by page provenance. Thumbnail/picture routes require current
source authorization, active document and matching source hash. Media-only
backfill is resumable; unsupported formats or PDFs above the optional 200 MiB
media ceiling retain excerpts without pictures.

Web visuals are an explicit JSON-provider capability opt-in, disabled by default.
The provider supplies image URLs, original source-page association and attribution;
public URL checks precede admission. Model-managed search supplies no image
inventory. Neither a related image nor its URL proves the depicted subject.
Clients require a separate load click for external pictures and attach no service
token. There is no arbitrary remote-image proxy or private-context image search.

## Contract and compatibility

The envelope is `{version:1,blocks:[...],assets:[...],targets:[...]}` in turn result,
`response.final` and historical `answer_details.rich_response`. Existing complete
text, narrative segments, citations, evidence and diagnostics remain available.
Narrative blocks reference accepted segment IDs in their original order; no
second independently rewritten narrative is stored. Typed navigation merges with
original source evidence so citation rectangles/provenance remain available.

Source/asset/target IDs are backend-issued references, not credentials. Every
non-narrative block has a readable fallback; unique block facts are included in
message text, subsequent conversation context and rapport input. User/commentary
messages have no rich envelope. Unknown or malformed optional versions fall back
to the complete saved text in clients and history projection. No rich patch is
sent after final commitment. Failed/cancelled turns retain durable commentary.

Limits: eight blocks, four image assets, twenty targets, one table with at most
eight columns/twenty rows and 64 KiB of rich JSON. Individual field/excerpt/string
bounds are schema-enforced. Optional discovery/final-image checking shares the
configured media deadline within the remaining total turn deadline. Status adds
`resolving_media` and `composing_response` without changing coarse turn statuses.

The canonical wire fixture is
`shrecknet/tests/fixtures/companion-rich-response.v1.json`, copied into Foundry,
SDK and ShreckOS contract fixtures for independent-package checks. The backend
regression checks schema/fixture synchronization. Refresh SDK schema/OpenAPI with
`shrecknet/scripts/export_character_companion_openapi.py --output PATH
--sdk-schema-output PATH` from the backend package with its configured import path.

## Verification and delivery gates

Automated coverage includes private history/final-event equivalence, owner
isolation, stable IDs, exact excerpts/physical page labels, null versus zero,
source-label property projection, block rejection, optional timeout, protected
file confinement, changed assets, v1/v2 Foundry negotiation and idempotency,
source-associated web metadata, future-version fallback and shared fixtures.
ShreckWorld tests cover protected thumbnails, mismatched document/source hash,
foreign ontology, inactive/deleted sources, bounded media references and unchanged
embedding metadata. Client tests cover the six block types, citation association,
local Foundry permission/revision checks, sorting, image expansion/failure,
reloading and text fallback. Existing Companion admission/recovery/browser suites
remain applicable. See the [validation record](Validation.md#rich-response-acceptance)
for commands/results and environment limitations.

Deployment acceptance remains distinct from local implementation:

1. Run a canary with real source storage, API/workers and both clients. Verify
   current-user source access, image loss/replacement and historical navigation.
2. Evaluate a fixed grounding corpus covering canon/rules/mixed sources, wrong
   edition/world, contradictory/no evidence, illustrated books and casual chat.
   Humans must confirm whether pictures actually depict any claimed subject.
3. Measure queue time, first status/commentary, final verified reply, per-stage
   p50/p95, tokens/cost, payload size, rejected blocks, broken assets and optional
   timeout rate. Retain the redesign's latency targets; no live benchmark numbers
   are claimed by mocked tests. The benchmark exporter includes rich block/asset
   counts, serialized bytes and media outcome in each turn record.
4. Check real Foundry v14 Actor/Item/Journal sheets as GM and player, compact and
   expanded, including a different table, revoked permission and changed artwork.
5. Run WebKit checks on a host with the required shared libraries. This local
   host can run Chromium/mobile and Firefox but lacks WebKit system dependencies.

## Operations and rollback

Deploy ShreckWorld contracts/media delivery, Shrecknet API/workers, SDK and both
clients together. Rich responses default enabled for new/missing settings.
Existing installations with a saved false value can enable
`companion_rich_response_enabled` in Administration's Companion group or `/config/`. Preserve the existing six model settings and saved
pipeline choice. `companion_rich_response_timeout_s` defaults to two seconds;
`companion_web_images_enabled` defaults false and requires a capable JSON provider.
These flags never bypass ordinary per-turn web/source permission.

No new Companion SQL migration, historical rich backfill, graph rewrite or
re-embedding is needed. The redesign's durable-commentary migration remains a
prerequisite. Book media manifests are optional source-owned files and need no
SQL migration; existing source deletion removes them. Normal source backup
mounts should include the generated media directories; they are rebuildable.

Drain active turns for configuration/image cutovers. Disable rich composition to
stop new envelopes; retain stored envelopes and text fallback for old clients.
Do not remove source media or v2 commentary data to force a rollback. Roll back
through binaries compatible with durable commentary and additive result JSON.
