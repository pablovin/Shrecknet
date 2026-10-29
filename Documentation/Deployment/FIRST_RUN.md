# Protected first deployment

The local distribution enables `SHRECKNET_INSTALLATION_ENABLED=true`. This is
opt-in: existing deployments retain their bootstrap behavior unless enabled.
ShreckOS owns the guided interface; Shrecknet owns identity, ontology/agent
creation and durable installation state. ShreckLLM retains provider credentials
and model activation. ShreckRPG retains RPG data and configuration.

## Claiming the first owner

`python -m app.core.runtime_secrets` generates a stable 0600 `installation_claim`
file alongside the existing browser/JWT secrets when installation mode is enabled.
The default local bundle also enables
`SHRECKNET_INSTALLATION_LOCAL_CLAIM_ENABLED=true` and shares only that claim with
Nginx via its own restricted, read-only volume. Nginx injects
`X-Shrecknet-Installation-Claim` only on the exact claim route and only for
`Host: localhost:8080`. The backend accepts that handoff only for an explicitly
enabled HTTP loopback public origin. The browser submits account details without
receiving a claim token. No additional terminal command is needed.

Manual deployments keep the host-code flow. The distribution's optional
`docker compose run --rm claim` remains an operator recovery tool; it is not part
of normal local onboarding. It never retrieves a claim through a public API.
Disabling local mode or configuring a non-local public origin disables header
handoff; it does not weaken the existing requirement for owner proof.

`POST /auth/claim` accepts `BrowserRegistration` fields and an optional `claim_token` (32–256
characters) for manual deployments. It uses the same origin, CSRF cookie/header, content-type validation,
rate limit storage and no-store response handling as browser authentication.
Successful creation returns the normal `BrowserRegistrationResponse`, sets the
normal browser cookies and authenticates the owner. Password minimum is 12.
No caller-provided roles are accepted.

The registration service checks the claim before first-user creation whenever
installation mode is enabled, including calls through the legacy `/users/` route.
A process-shared file lock serializes first-user creation across API workers using
the same local volume. A successful claim always creates exactly the first owner;
a later claim is refused, even if normal public registration is allowed. The
owner is approved and email-verified by host credential possession or the trusted local handoff, so SMTP is
not required to begin. Later users retain the normal registration/email policy.
The claim route is disabled (404) outside installation mode. Invalid claim or an
already claimed installation returns 403; malformed input returns a sanitized 422;
CSRF/origin failures return 403 and excessive attempts return 429.

## Installation endpoints

All responses are `Cache-Control: no-store`. Except public status, the routes
require installation mode and an administrator bearer token. The server owns
completion and verification fields; the browser cannot write a pass flag.

| Endpoint | Input and behavior |
| --- | --- |
| `GET /setup/installation/status` | Public `{enabled, owner_required, claim_mode}` only; no settings or secrets |
| `GET /setup/installation/readiness` | Public, bounded health checks for the four application APIs; only service IDs and ready/starting status are returned |
| `GET /setup/installation/state` | Administrator `InstallationState` |
| `POST /setup/installation/world` | `{template: fantasy\|horror\|scifi, include_example: true}`; resumes starter ontology construction and returns the saved world |
| `PUT /setup/installation/models` | `{provider, model, jobs}`; requires a configured gateway model, creates/reuses supported world agents and saves target assignments |
| `PUT /setup/installation/progress` | `{tour_completed: boolean}` only |
| `POST /setup/installation/verify` | `{accept_provider_usage: true}`; atomically reserves one check and dispatches the worker task, returning 202 |
| `POST /setup/installation/complete` | Requires saved world, acknowledged introduction, and a passed worker/model check if model assignments were saved |

`jobs` accepts `elder`, `architect`, `novelist`, `librarian`, `character_agent`, and
`companion`. The service maps these capabilities to current model-target keys and
JSON repair. Initial assignments disable reasoning and web search. Existing
configuration APIs remain the place to specialize targets and runtime limits.
The gateway remains authoritative for model catalogs/activation. Configuration
errors return 422; incomplete setup or pending verification returns 409; an
unavailable queue returns 503. Error bodies never include provider credentials.

Selected Elder, Architect, Novelist and Librarian agents are created through
`AgentService` and linked to the saved world, or an existing active world/job
agent is reused. Embodied and personal companion creation needs domain-specific
inputs; selecting those capabilities configures their model roles only.

## Starter content and retries

`SetupService.resume_default_world` extends the established starter definitions
without changing legacy `create_default_worlds` skip-existing behavior. Installation
reserves the chosen template before creating data, refuses a pre-existing template-name collision, and serializes world creation. It adds missing definitions/relationships after an
interrupted attempt. The optional **Lantern Bridge** example uses
`OntologyInstanceService`, with a labelled story, NPC, place, source-derived scene
and milestone. It reuses the exact labelled source page and deterministic scene
on retry, then queues normal semantic indexing. Example content may be edited or
removed after setup. Do not rename/delete a partial example during retries.

World selection is persistent: once saved, retries return that world instead of
creating another. Existing independently authored worlds are not imported by
these endpoints. Indexing completion, retrieval quality and domain-agent results
are not inferred from successful creation.

## Persistence and verification

`InstallationRepository` keeps one typed record in `installation.db` under
`SHRECKNET_DATA_DIR`; its additive table is created transactionally on first use.
It stores world/example IDs, created agent IDs, setup progress, configured model
keys/fingerprint, verification ID/status/timing/error code, and completion.
No password, claim token, provider key or access token is stored in this record.
The normal authentication and domain stores are unchanged.

One `installation.verify_models` Celery task is sent to `ontology_linking`.
Reservations and worker state transitions prevent duplicate submissions/execution.
The task uses the existing ShreckLLM chat client, requests a short response from
each distinct configured model target and enforces a 480-second caller deadline.
An unconsumed/stalled check expires after 600 seconds when progress is read.
Changing a target invalidates the recorded pass. A terminal failure permits an
explicit new reservation; automatic task retries are disabled. Provider-side work
can outlive a caller timeout, so an explicit retry can incur additional usage.

A pass means queue → worker → gateway → model response worked for this check.
It does **not** certify Elder grounding, embedding completion, Architect
extraction, Novelist generation, library ingestion or character workflows.
Those need their own real inputs and acceptance tests. The frontend must preserve
that distinction in labels and release claims.

## Operations and compatibility

The portable frontend-repository distribution assembles all services and generates
Neo4j/internal-service credentials as well as authentication keys into a private
named runtime volume. It exposes only the loopback frontend port. Existing backend
Compose and configuration files remain supported. No existing database or host
media directory is repurposed by the new distribution.

Back up installation/configuration/domain/media/runtime-secret volumes together.
Disabling installation mode removes setup access but does not erase progress or
created data. An older backend can ignore `installation.db`; rollback does not
undo created ontologies, examples or agents. Existing domain migration rules still
apply to upgrades. No coordinated full-stack restore or public-hosting automation
is implied by this fresh-install feature.

See the frontend repository's `shreckos/documentations/getting-started/FIRST_RUN.md`
for bundle assembly, local startup, image-only packaging and current release gates.
Python SDK support is described in [installation SDK](../../python_sdk/docs/getting_started/installation.md).
