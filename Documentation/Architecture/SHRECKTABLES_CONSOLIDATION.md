# ShreckTables consolidated backend (implementation branch)

ShreckTables is the authoritative **live RPG table** subsystem, separate from
world/ontology definitions and from Game Engine ruleset publication.

## Architecture and ownership

- **Shrecknet**: canonical World, Ontology and User identities. `UserRole.GM`
  allows table creation but does not confer other world-management permissions.
  Legacy `world_builder` and `writer` remain in place until reviewed account
  migration, as they are used by unrelated services.
- **ShreckWorld / ShreckTables**: a separate SQLite table domain for world-scoped
  groups, memberships, session scheduling, votes, attendance, Google integration,
  private attachments and legacy ID mapping. It uses external IDs for Shrecknet
  identities, not cross-database foreign keys.
- **ShreckOS**: dedicated Apps management UI and Nexus player experience using
  the new `/api/v1/shrecktables` API through its ShreckWorld proxy.
- **ShreckRPG**: legacy source for the migration; Notes, Activities and other
  consumers must remain functional until their legacy game references are
  reconciled. The original database must not be deleted at cutover.

## Shared world knowledge

Every table resolves the active ontology of its Shreckworld. Same-world tables
share Atlas, narrative and explanatory sources, and the rules selected in
Game Engine. A table GM can publish the reviewed account of a session to the
world narrative library. The source can then be used in Architect's existing
graph workflows. Original session documents remain within the table.
See the [world identity contract](../API/SHRECKWORLD_IDENTITY_API.md).

## Authorization

Global Admin can manage all tables. A global GM can create tables and becomes
their first table-scoped GM. Within a table, any GM can invite a member,
promote/demote a GM, manage settings, create/finalize sessions and upload
attachments. Players can access their own tables, vote/unvote, confirm
attendance and retrieve authorized session documents. GM management is
server-side; hiding controls in the UI is never sufficient authorization.

There must always be at least one GM. Authorization includes table access on
every session, poll, attachment and Google operation.

## Google one-click administrator setup

The Google Cloud project owner must configure one OAuth consent application and
authorized HTTPS callback URI. Deployment environment variables:

| Environment | Purpose |
|---|---|
| `SHRECKWORLD_GOOGLE_CLIENT_ID` | OAuth web client ID |
| `SHRECKWORLD_GOOGLE_CLIENT_SECRET` | OAuth web client secret |
| `SHRECKWORLD_GOOGLE_REDIRECT_URI` | Exact public callback, e.g. `https://your-host/shreckworld-api/api/v1/shrecktables/google/oauth/callback` |
| `SHRECKWORLD_GOOGLE_FRONTEND_RETURN_URL` | Fixed Apps URL to return after consent |
| `SHRECKWORLD_GOOGLE_TOKEN_FERNET_KEY` | Generated Fernet encryption key |

Generate a key once and retain it in your secrets manager (not Git):

```shell
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

**Do not change the key without re-encrypting saved refresh tokens.** Restrict
read-access to the deployment secrets. Google OAuth refresh tokens are encrypted
before database persistence.

The GM then uses **Apps → ShreckTables → Integrations → Connect Google**,
selects/consents to the account, and creates the table calendar. Other members
do not need Google logins to vote or RSVP.

OAuth permissions include Calendar event creation/editing, app-created calendar
management, Meet space read/settings and Drive read for generated transcript/
Gemini documents. Google Workspace edition, organizer settings, domain admin
policies, and Google OAuth verification may restrict the end-to-end flow.
These scopes should be reviewed and minimized during external consent
verification; do not deploy into a company domain without permission.

## Calendar synchronization

Creation, editing, cancellation and poll finalization mark the affected
session pending. Celery jobs synchronize with Google Calendar. Each new event
uses an idempotent event ID so retries can recover lost responses. Existing
events are patched to preserve conference details.

The `shrecktables_scheduler` container runs Celery Beat every 15 minutes;
reconciliation picks up pending/failed/partial Calendar events and rechecks
completed Meet sessions for transcript/Gemini notes.

Failures are explicit in `calendar_sync_status` / `calendar_sync_error`;
they must never be presented as success. An existing event ID imported from
ShreckRPG is **never automatically recreated** during import.

Google-generated files are saved as private attachments with a unique
per-session/source/document ID and a link to the original Google document.
Only GMs can upload and trigger imports. All table participants can read
attachments if permitted by table membership; further per-file ACLs are
future work.

**Operational caveat:** The Google OAuth, Calendar, Meet and Drive APIs require
real Workspace testing. No live credentials or Google domain policy have been
provided in this branch. The implementation and CI do not prove that a
Workspace organizer can enable Smart Notes/Transcription automatically.

## Data migration

The repository Compose launcher exposes the migration as an explicit startup
operation. This is the usual command when rebuilding and starting the stack:

```bash
./scripts/compose_up.py --build --migrate-shrecktables
```

For an already-built stack, omit `--build`; the migration option starts the
stack detached, waits for Compose health checks, and then runs the preview
inside `shreckworld_api` with an interactive terminal. The ShreckRPG database is read-only at
`/legacy-shreckrpg-data`; set
`SHRECKNET_BACKUP_SHRECKRPG_HOST_DIR` in `.env` if it is not at the default
`./shreckrpg/databases` path.

The command reads ShreckRPG and Shrecknet databases read-only and prints a
per-table report with ontology/world mapping, distinct users, sessions,
attendance, polls and votes, plus source totals and field coverage. Ontology and
world names are compared case-insensitively after trimming and collapsing
whitespace. A match must be unique, and an existing ontology `world_id` must
agree with that name match. The target table uses the canonical world's sole
ontology ID; worlds with zero or multiple linked ontologies are blocked because
they have no unambiguous active ontology. Unmatched, ambiguous, conflicting and
missing-user tables are shown as blocked. Attendance or vote users who are no
longer table members are also reported; any such association issue prevents the
whole apply so access is not broadened silently.

The report groups fields into migrated, defaulted/derived, and not migrated.
Table name, owner, members/roles, calendar ID and VTT URL are retained. The
legacy ontology ID is resolved by name; ShreckTables stores the matched world's
canonical ontology ID. Session
title, summary, location, schedule/timezone, existing Google event/Meet IDs,
attendance, polls, options, votes, finalized options, and source timestamps are
retained where present. Legacy IDs are stored in `shrecktables_legacy_map`.
Table description/image/timezone and session duration/status use documented
defaults or derivation. No Google events are recreated or modified. ShreckRPG
Notes/Activities, external calendar ownership, and files outside the RPG
session schema are not migrated by this command.

The preview makes no durable writes. To apply, acknowledge a restorable backup
of all three databases and type `MIGRATE SHRECKTABLES` at the prompts. Run this
during a maintenance window with ShreckRPG table/session writes stopped. The
importer is idempotent: repeat runs use legacy ID mappings and fill missing
related records without duplicating them. It does not modify the original
ShreckRPG data, which must remain available for legacy Notes/Activities and
other references.

For direct operator use, `python -m scripts.migrate_shrecktables` remains
dry-run by default. `--interactive` enables the same confirmation flow;
non-interactive `--apply` requires both `--backup-confirmed` and the exact
`--confirmation "MIGRATE SHRECKTABLES"` value.

**Important unresolved cutover dependencies:**

1. Existing ShreckRPG Note/Chronicle and Activity records still reference
   legacy `game_id` values. Rewrite their consumers or retain a compatible
   mapping before deleting any legacy game table.
2. Existing Google event IDs can only be patched after a newly authorized
   organizer has write permissions on their original calendars. Reconnecting
   a different account does not transfer event ownership.
3. Existing external deep links with numeric legacy game IDs may need
   redirect mapping to canonical table IDs; do not change those links silently.
4. Before disabling legacy writes, compare table, member, session, poll,
   option, vote, attendance and Google event counts and perform a test
   migration on a database snapshot.
5. Keep the original datasets restorable throughout rollout.

## Validation

Run `pytest tests/test_shrecktables.py` from `shreckworld`.
The ShreckOS companion PR runs the frontend tests, lint and production build.
Test Google OAuth against a real authorized Workspace account, including
consent, calendar creation, Meet setup, completion, notes/transcript delivery,
revocation, retries, timezone/DST cases and multiple GMs.

**Deployment policy:** do not merge both PRs separately. First validate the
canonical data and external integration, then deploy backend + migrations,
and finally deploy the Nexus/Apps frontend cutover.


### Shared-world consistency audit

World managers can call `GET /api/v1/shrecktables/worlds/{world_id}/knowledge-integrity`
to verify that table and published-session-source references point to the world's
single active ontology, that the world rules selection is unambiguous, and to
inspect source/collection counts. The endpoint does not modify data. A
`consistent: true` result **only** covers those relational references; it
explicitly returns `graph_and_vector_store_verified: false` because Neo4j,
Qdrant and original media files require their own cutover verification.

New table creation rejects worlds with conflicting legacy table rules until
the world manager selects one shared published rules version.
