# Foundry character sheets on Shrecknet: design

**Status: proposed design, not implemented.** No code, endpoint, table or module behavior
described here exists yet. This page records the agreed direction and the proposed
contract, so that the implementation slices can be reviewed against it. Open questions
are listed at the end.

## Purpose

Players should be able to see their Foundry VTT character sheet on the Shrecknet website
and make a small set of edits there. Foundry then applies those edits to the real sheet.

The website sheet is a **companion** to the Foundry sheet, not a replacement. Foundry
game systems already implement their own rules, calculated values and procedures (for
example Pendragon's winter phase) and work well. Shrecknet shows the whole sheet, allows
edits that need no game rules, and leaves everything else to Foundry.

A later phase may let Shrecknet run procedures a Foundry system does not cover (for
example horse herding in Pendragon) and write the outcome to the Foundry sheet. That is
out of scope here. The change queue below is designed so it can carry such outcomes
without a redesign.

## Decisions

| Topic | Decision |
|---|---|
| Owner | Shrecknet core. The sheet is attached to the character's entity in the Shrecknet graph (for example the existing "Cadwallon" entity). |
| ShreckWorld / ShreckTable | Not used. ShreckTables require a published ruleset, and rulesets come after rulebook-ontology learning. A future ShreckTable (one per ShreckWorld) may wrap this feature. The ShreckTable rule that participant links grant no action authority is unchanged. |
| Assignment | A Shrecknet administrator assigns a user to a character. World builders cannot assign in this phase. A dedicated GM role may be added later. |
| Approval | None. An assigned player's edits are accepted immediately and wait until a Foundry client can apply them. |
| Who applies edits | Any connected Foundry client allowed to update the actor: a GM, or the player who owns the actor. Foundry lets an actor's owner update the actor and its embedded items. |
| Foundry setup key | Unchanged. Keys stay scoped to Shrecknet Worlds through `allowed_world_ids`. |
| Systems | Pendragon only (system id `Pendragon`, Foundry v14). The field mapping is per system so that rulebook-derived ontologies can replace it later. |
| Website UI | Built in the ShreckOS frontend (`ShrecknetFrontend` repository). This repository owns the backend API and the Foundry module. |

## Concepts

- **Character entity:** an entity node in a Shrecknet ontology instance, identified by
  `instance_id` and `entity_instance_id`. It already exists independently of Foundry.
- **Assignment:** a user is allowed to view and edit the sheet of one character entity.
- **Actor link:** the binding between a character entity and one Foundry actor,
  identified by `foundry_world_id` (Foundry's `game.world.id`) and the Foundry actor ID.
- **Snapshot:** the latest copy of the Foundry actor, pushed by the module. It holds the
  actor's stored data and the values Foundry calculates from it.
- **Change:** a request to modify the Foundry actor, created on the website and applied
  by a Foundry client.

## Why the sheet is not stored as a node property

Entity property values are stored as one JSON string (`properties`) on the entity node
and are loaded whenever an instance is read in full (`OntologyInstanceService`). A
Pendragon actor exported from Foundry is about 150 KB, and the module would update it
after every change in play. Storing it there would grow every full instance read and
rewrite the node frequently.

The proposed storage is therefore a set of SQL tables in Shrecknet, keyed by
`instance_id` and `entity_instance_id`. The sheet stays associated with the node, but
lives outside it. The existing property type
`PropertyDataType.FOUNDRY_JSON = "foundry_character_sheet_json"` is not used for the
snapshot. Whether the node should carry a small marker or summary is an open question.

## Calculated values

Much of what a player wants to read is not in the stored actor data. Foundry calculates
it when the actor loads. Measured on the Pendragon pregen Sir Cadwallon (Pendragon 14.25):

| Value | Stored | Calculated by Foundry |
|---|---|---|
| Hit points | `hp.value 10`, `hp.max 10` | `hp.value 11`, `hp.max 27`, plus `knockdown`, `majorWnd`, `unconscious` |
| Glory | not stored as one value | `system.glory` (2275), from history items and passive Glory |
| Age | `born 489` | `age 19`, using the world setting `Pendragon.gameYear` (508) |
| Stats | parts such as `value`, `culture`, `create`, `winter` | `stats.*.total`, `statTotal`, `damage`, `healRate` |
| Skills, passions, traits | parts plus an `XP` checkbox | item `total`; traits also `oppvalue` (for example Valorous 16 / Cowardly 4) |

The module must send these calculated values with every snapshot. Shrecknet must not
recalculate them, because that would re-implement the game system.

## Proposed contract

### Persistence (Shrecknet SQL, additive)

`character_sheet_assignments`
: `id`, `instance_id`, `entity_instance_id`, `user_id`, `created_by`, `created_at`.
  Unique on (`instance_id`, `entity_instance_id`, `user_id`).

`character_sheet_links`
: `id`, `world_id` (Shrecknet World), `instance_id`, `entity_instance_id`,
  `foundry_world_id`, `foundry_actor_id`, `system_id`, `system_version`, `created_by`,
  `created_at`. Unique on (`instance_id`, `entity_instance_id`) and on
  (`foundry_world_id`, `foundry_actor_id`).

`character_sheet_snapshots`
: `link_id` (one row per link), `revision` (increases on every accepted push),
  `source` (JSON: the actor's stored data), `derived` (JSON: calculated values chosen by
  the system mapping), `pushed_by`, `pushed_at`.

`character_sheet_changes`
: `id`, `link_id`, `author_user_id`, `origin` (`player_edit` now; later values such as
  `companion_procedure`), `operations` (JSON, see below), `base_revision`, `status`
  (`pending`, `applying`, `applied`, `rejected`, `cancelled`), `lease_expires_at`,
  `applied_by_user_id`, `result` (JSON: per-operation outcome or error), timestamps.

### Snapshot contents

The module sends the actor's stored data with these removals:

- `flags`, except an explicit per-system allow-list (empty for Pendragon);
- `ownership`;
- item `system.GMdescription`, which Pendragon uses for GM-only text. Players must never
  receive it.

Item and actor HTML descriptions are kept for display. Shrecknet sanitizes them before
the website renders them.

### Change operations

A change holds an ordered list of operations. Each operation states the value it expects
to replace, so a change made against an outdated snapshot can be rejected instead of
overwriting a newer value set in Foundry.

```json
{
  "operations": [
    { "op": "set", "document": "item", "item_id": "abc123",
      "path": "system.XP", "expected": false, "value": true },
    { "op": "set", "document": "actor",
      "path": "system.motto", "expected": "Faith and charity", "value": "Faith above all" },
    { "op": "create_item", "item_type": "history",
      "data": { "name": "Battle of Badon Hill", "system": { "year": 508, "glory": 100 } } }
  ]
}
```

The backend validates every operation against the system's editable list before the
change is stored. The module checks the same list again before applying it. The list is
defined once in the backend and served to the module, so it is not duplicated in
JavaScript.

### Editable fields (Pendragon, phase 1)

| Document | Field | Edit |
|---|---|---|
| Skill, passion, trait item | `system.XP` | Tick or untick the experience checkbox |
| Trait item | `system.oppXP` | Experience checkbox of the opposite trait |
| Actor | `system.background`, `system.features`, `system.motto`, `system.heraldry` | Narrative text |
| New `history` item | `name`, `system.year`, `system.glory`, `system.description` | Add a history entry; Foundry recalculates Glory |

Everything else is read-only on the website, including stat, skill, passion and trait
values, hit points, money, equipment, horses and squires.

### Proposed endpoints

Admin:

- `PUT /character-sheets/assignments` with `{instance_id, entity_instance_id, user_id}`;
  `DELETE` to remove one; `GET` to list them by entity or by user.
- Creating and removing actor links (see open question 3 for where this happens).

Assigned player or admin:

- `GET /character-sheets/mine`: sheets assigned to the caller.
- `GET /character-sheets/{link_id}`: latest snapshot, its revision and pending changes.
- `POST /character-sheets/{link_id}/changes`: create a change. Returns `409` when an
  operation's `expected` value no longer matches the latest snapshot.
- `POST /character-sheets/{link_id}/changes/{id}/cancel`: cancel the caller's own
  pending change.

Foundry module, authenticated with the signed-in user's own bearer token:

- `PUT /character-sheets/{link_id}/snapshot`: push a snapshot. Accepted from an
  assigned user or an admin.
- `POST /character-sheets/changes/claim` with `{foundry_world_id, actor_ids[]}`: atomically
  move pending changes for those actors to `applying` with a short lease, so a GM and the
  owner who are both online never apply the same change twice. Expired leases return to
  `pending`.
- `POST /character-sheets/changes/{id}/result`: report `applied` or `rejected` with
  per-operation results.

## Flow

1. An admin assigns a user to the character entity and links the entity to a Foundry
   actor.
2. When a linked actor or one of its items changes in Foundry, a connected client allowed
   to update it pushes a new snapshot (debounced). It also pushes one when the world
   loads.
3. The player opens the sheet on the website and creates a change.
4. A connected Foundry client claims pending changes for linked actors it can update.
   For each operation it compares `expected` with the actor's current value, applies it
   with `actor.update`, `item.update` or `actor.createEmbeddedDocuments`, and reports the
   result. Operations whose expected value no longer matches are rejected, not applied.
5. The client pushes a fresh snapshot, so the website shows the recalculated values.

Ownership in this repository follows the existing layering:

- routers in `shrecknet/app/api/routers/` (transport and authorization);
- schemas in `shrecknet/app/schemas/`;
- business rules in a new `CharacterSheetService` under `shrecknet/app/services/`;
- persistence in `shrecknet/app/repositories/`;
- per-system mappings (editable list, calculated values to keep, removed fields) in one
  module per system.

The Foundry module gains a character-sheet sync component next to its existing API
client in `integrations/foundry/shrecknet-foundry/scripts/`.

## Side effects and operations

- **Module behavior changes.** The module currently states that it never serializes
  system data or flags (`scripts/apps/foundry-context.js`) and that it does not create
  or modify Actors or Items. For linked actors only, it will serialize system data and
  update actors. The module README, `Documentation/Deployment/FOUNDRY_MODULE.md` and the
  module release notes must say so.
- **Trust boundary.** A snapshot comes from a Foundry client controlled by a GM or the
  owning player. It is a mirror for display, not an authoritative record. A player able
  to forge a snapshot can already edit the actor directly in Foundry.
- **Offline behavior.** When no allowed client is connected, changes stay `pending`. The
  website shows them as pending, with the time they were created.
- **Backups.** The new tables live in the Shrecknet SQL database and are covered by its
  existing backup.

## Compatibility and migration

All persistence is additive. No existing endpoint, ontology property, graph node or
ShreckTable behavior changes. The public Python SDK, its docs and examples gain the new
endpoints when they ship.

## Delivery slices

Each slice ships code, tests and documentation together, as required by `AGENTS.md`.

1. **Backend:** assignments, actor links, snapshot storage and read endpoints.
2. **Module:** actor linking support and snapshot push (stored data plus Pendragon
   calculated values).
3. **Backend:** change creation, validation against the editable list, claim, lease and
   result endpoints.
4. **Module:** claiming and applying changes, then pushing a fresh snapshot.
5. **ShreckOS (separate repository):** sheet view and the phase 1 edits.

## Open questions

1. **Storage:** is a separate SQL table keyed by the entity acceptable, rather than the
   `foundry_character_sheet_json` property on the node? Should the node carry a marker
   (for example "has sheet") or a few headline values?
2. **Website UI:** will the sheet screens be built in ShreckOS, and by whom?
3. **Actor linking:** should an admin link an entity to a Foundry actor from the website
   (choosing from actors the module reports), or from Foundry by a GM signed in as a
   Shrecknet admin?
4. **Visibility:** can players see other players' sheets in the same World, or only
   their own? The proposed default is only their own, plus admins.

## Related documentation

- [Shrecknet Architecture](SHRECKNET_ARCHITECTURE.md)
- [Foundry module deployment](../Deployment/FOUNDRY_MODULE.md)
- [Foundry Integration API](../API/FOUNDRY_INTEGRATION_API.md)
- [ShreckWorld game engine API](../API/SHRECKWORLD_GAME_ENGINE_API.md) (ShreckTable)
