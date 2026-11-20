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

From the `shreckworld` directory, first stop table/session writes, take a
consistent backup of the ShreckRPG, Shrecknet and ShreckWorld SQLite DBs, and
initialize the new ShreckWorld schema:

```shell
python -m scripts.migrate_shrecktables \
    --rpg-db ./path/to/shreckrpg.db \
    --shrecknet-db ./path/to/shrecknet.db \
    --target-db ./path/to/shreckworld.db
```

This is a **dry-run by default**. It reports counts and blocks applying data
without a mapping from each legacy ontology ID to a canonical World ID.
Legacy world resolution can be overridden with a JSON `--world-map`:

```json
{ "7": "canonical-shrecknet-world-uuid" }
```

After validating the dry-run, run with `--apply`. Idempotent
`shrecktables_legacy_map` links old game/session/poll/vote IDs to new IDs.
The importer does not modify ShreckRPG or recreate any Google Calendar event.

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
