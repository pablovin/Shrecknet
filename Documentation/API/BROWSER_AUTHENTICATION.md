# Browser authentication and sessions

Shrecknet supplies additive cookie-session contracts for the user-facing ShreckOS application. They are disabled by default for direct/non-Compose runs; the root Compose deployment enables them by default. Shrecknet remains the identity owner; ShreckRPG and ShreckWorld do not store refresh credentials or passwords. Existing `/auth/token`, `/auth/jwks`, `/users/`, and SDK bearer login remain compatible.

## Private configuration and deployment

`app/core/browser_auth.py` reads private `SHRECKNET_BROWSER_` settings. They are not part of the public/global configuration API:

| Environment variable | Default / meaning |
| --- | --- |
| `SHRECKNET_BROWSER_ENABLED` | `false` in direct/non-Compose runs; root Compose enables it by default |
| `SHRECKNET_BROWSER_SECRET` | Optional override; otherwise generated once and persisted for all API/worker containers |
| `SHRECKNET_BROWSER_ALLOWED_ORIGINS` | Optional JSON list override; otherwise the exact `SHRECKNET_PUBLIC_ORIGIN` |
| `SHRECKNET_BROWSER_SECURE_COOKIES` | Inferred from `SHRECKNET_PUBLIC_ORIGIN`: `true` for HTTPS, `false` for HTTP. HTTP is still restricted to loopback origins. |
| `SHRECKNET_BROWSER_ACCESS_MINUTES` | 10, allowed range 1–30 |
| `SHRECKNET_BROWSER_PERSISTENT_DAYS` | 30, allowed range 1–90 |
| `SHRECKNET_BROWSER_PERSISTENT_IDLE_DAYS` | 7, allowed range 1–30 |
| `SHRECKNET_BROWSER_SESSION_HOURS` | 12, allowed range 1–24 |
| `SHRECKNET_BROWSER_SESSION_IDLE_HOURS` | 2, allowed range 1–12 |

Production secure-cookie routes also require persistent `SHRECKNET_JWT_PRIVATE_KEY_PEM` and `SHRECKNET_JWT_PUBLIC_KEY_PEM` key material. The root Compose stack generates and persists an RSA keypair when these overrides are absent. Its `shrecknet_runtime_secrets` one-shot service writes the Fernet secret and keypair under `shrecknet/databases/runtime-secrets/` with owner-only permissions before the API and worker start. API and worker containers read the same files; generated material is never put in frontend variables or container logs. These deployment bootstrap settings are sourced from environment variables or runtime secret files, not overridden by stale copies in the runtime configuration database. Keep this directory with the identity database backups. Removing it invalidates existing JWT signing keys and browser-auth ciphertext; do not delete it to troubleshoot startup. Custom key overrides must provide both matching PEM values.

Shrecknet and ShreckOS are separate Compose projects with separate environment files. Copy `shrecknet.env.example` to Shrecknet's repository-root `.env`; ShreckOS uses `.env.shreckos` in its own repository. Each project reads only its own file. Set `SHRECKNET_PUBLIC_ORIGIN` in Shrecknet's `.env` to the website origin; it defaults to `http://localhost` for local development. Set the corresponding origin in ShreckOS's `.env.shreckos` as well. For a public deployment both values must match exactly, for example `https://shrecknet.club`, with any non-default port and no path or trailing slash. The backend value supplies browser-origin validation, selects cookie mode (`Secure` for HTTPS; non-Secure cookies are accepted only for loopback HTTP), and provides the email-verification URL base. The Shrecknet Compose stack ignores legacy browser-origin/cookie overrides so the backend's one setting controls those values. Browser auth is enabled by Compose. The normal API startup creates the additive browser-auth tables through `app/db/init_db.py`; the API Compose healthcheck requires both `/health` and `/auth/public` to succeed before the service is considered ready. Start Shrecknet from its repository root with `docker compose --env-file configs/compose.env --env-file configs/neo4j.env --env-file .env up --build -d`. The Compose projects remain operationally connected through the external Docker network and service aliases; ShreckOS requires Shrecknet's API, Redis, ShreckWorld, and ShreckLLM services to be running, but it does not load Shrecknet's env file. For local non-Compose HTTP development, configure `SHRECKNET_PUBLIC_ORIGIN=http://localhost:5174` or keep the explicit `SHRECKNET_BROWSER_` overrides; never use insecure cookies for a non-loopback host.

If overriding generated secrets, configure them in the root `.env` or `configs/compose.env` so both the one-shot provisioner and every API/worker container receive the same values. Set both JWT PEM variables together. Back up `/data/runtime-secrets` alongside the identity database. Rotating the Fernet secret invalidates pending CSRF/retry ciphertext; rotating JWT keys requires the normal key-overlap/JWKS-cache plan. Do not log or put private keys/Fernet secrets into frontend variables or source control.

Serve the frontend over HTTPS with a same-origin `/api/shrecknet/` reverse proxy. Preserve Origin, Authorization, `X-CSRF-Token`, `X-Request-ID`, and all `Set-Cookie` headers. Disable proxy/CDN/service-worker caching for authentication. Configure trusted ASGI proxies and strip untrusted forwarded headers at ingress so rate-limit addresses reflect the real client. Never forward refresh cookies to RPG, ShreckWorld, or third parties.

Email verification links use `<SHRECKNET_PUBLIC_ORIGIN>/auth/verify-email` by default. An explicitly configured `email_verification_frontend_url` remains an optional override. SMTP credentials and existing account-creation policy are still configured separately. The public registration path requires an already-bootstrapped instance; it does not create the first administrator.

## Endpoints

All successful and handled-error responses from these routes use `Cache-Control: no-store`. Mutations require JSON, an explicitly allowed Origin, and a matching signed CSRF cookie/header context bound to the current refresh session ID. Obtain it through `/auth/csrf` before a mutation. `SameSite` is defense in depth, not the sole CSRF control.

| Route | Input and result |
| --- | --- |
| `GET /auth/public` | Returns creation mode, verification requirement, new-browser password minimum (12), and persistent lifetime defaults. |
| `GET /auth/csrf` | Sets/reuses a short-lived signed HttpOnly preauth/session-bound CSRF cookie and returns `{csrf_token}`. Does not authenticate or renew a session. |
| `POST /auth/login` | `{identifier,password,remember_me}`; accepts existing username/email and eligible password credentials, returns `BrowserSessionResponse` and refresh cookie. |
| `POST /auth/register` | `{full_name,username,email,password,timezone,remember_me}`; username 3–150 ASCII letters/digits/`_.-`, password 12–1024 characters, real IANA timezone, no role/approval/entity fields. 201 result is `authenticated` with session, `verification_required`, or `pending_approval`. |
| `POST /auth/refresh` | Empty JSON, refresh cookie, CSRF context, and UUID `X-Request-ID`; atomically rotate credential and issue current identity/access. |
| `POST /auth/logout` | Empty JSON; possession of current or previously-issued family credential permits revocation. Clear auth cookies and return 204. Missing-session logout is harmless with valid preauth CSRF. |
| `GET /auth/session` | Bearer-protected live identity summary; new `sid` tokens must pass session/eligibility/expiry checks. Used by RPG/World. |
| `POST /auth/verify-email` | `{token}`; consume an existing valid verification token; confirmation does not bypass moderation or issue a session. |
| `POST /auth/resend-verification` | `{email}`; 200 `{accepted:true}` without revealing whether an account exists. |

`BrowserSessionResponse` contains `{user, access_token, token_type:"bearer", access_expires_at, session_expires_at, idle_expires_at, csrf_token}`. Dates are UTC timestamps. `user` contains only `id, username, full_name, email, timezone, role`. Neither response nor logs include a refresh token, password, password hash, or service configuration.

Production refresh cookies are `__Host-shrecknet_refresh; Secure; HttpOnly; SameSite=Lax; Path=/`, without `Domain`; CSRF uses the same protections with its own cookie name. Remembered sessions get a bounded Max-Age; unchecked sessions omit persistent expiry. Local HTTP uses distinct `shrecknet_dev_refresh`/`shrecknet_dev_csrf` names, and the validator rejects non-loopback HTTP origins. Browser session restoration can retain session cookies: server idle/absolute expiry is authoritative.

Handled errors use `{"detail":{"code":"..."}}`: 401 invalid credentials/session; 403 approval/verification restriction, closed registration, or invalid CSRF/origin; 409 account conflict or concurrent refresh; 415 inappropriate media type; 422 invalid input/request ID; 429 throttling with Retry-After; 503 disabled/unconfigured/dependency unavailable. Password/token inputs are removed from validation errors. Existing account-status codes remain `invalid_credentials`, `pending_approval`, `account_not_approved`, `email_not_verified`. Account status is returned only after verifying a password.

## Rotation, revocation, and persistence

`AuthSession` owns one revocable browser session/family ID, user ID, current refresh digest, creation time, idle/absolute expiry, persistence choice, and revocation timestamp. `AuthRotation` retains issued-digest history to detect old-credential reuse. Refresh values use random secrets; only SHA-256 digests are stored in clear. `AuthRateLimit` persists counters across workers. These are new additive tables with indexes; no existing identity rows or IDs are replaced.

Rotation uses a conditional database update on current digest and active expiry/revocation state. A replacement is encrypted with the private Fernet secret in the retry record. For 30 seconds, repeating the same request ID and prior credential can recover the same still-current replacement after a lost response. A different concurrent request receives `409 refresh_conflict`, does not change cookies, and does not revoke a legitimate session; wait for the browser's shared cookie update before a fresh serialized request. After the replay window, reuse of an issued prior credential revokes its family. Unknown random credentials cannot revoke a family just by naming its ID.

ShreckOS uses Web Locks when available, with server-side CAS/conflict handling as the correctness boundary. It retries only renewal for a lost response; password and registration submissions are not replayed. A successful new login/eligible registration revokes the previous cookie family before replacing it, including a previously pending logout. Signing out invalidates late frontend responses through an auth generation. If the logout response is lost, a local nonsecret pending marker blocks automatic restoration until sign-out is confirmed or the user deliberately signs in.

The shared eligibility predicate is used by password login, renewal, and browser protected access. Password/email/role updates revoke affected browser sessions; rejected/ineligible/deleted users fail live validation. New access tokens carry `sid` and have expiry capped by the session's idle/absolute limits. Shrecknet checks these against its database; updated RPG/World consumers call `/auth/session` for each new `sid` request and fail closed as 503 on identity outage or 401 on revocation/identity change. This does not cancel already-running operations. Legacy bearer tokens without `sid` retain existing lifetime/validation semantics and are not retrospectively made revocable.

Limits are persisted per client/identifier capability: login 30/IP/minute and 10/identifier/minute; registration 5/IP/5 minutes; renewal 60/session/minute and 120/IP/minute; verification 20/IP/minute; resend 10/IP/5 minutes and 1/email/minute. Counter keys are keyed digests, not clear email/IP values. Legacy routes retain their existing limits/behavior; do not claim these new-route limits cover all legacy authentication surfaces. Logs contain method, route, status, and elapsed milliseconds, suitable for aggregate outcome/latency monitoring; do not add request/response bodies or credentials.

## Schema, cleanup, and rollout

Back up the identity database. Apply the additive tables through normal Shrecknet initialization (`app/db/init_db.py` registers these models) before enabling browser auth or running updated password/account mutation flows. Disable the feature while preparing schema and deploy all changed consumers together. Test the exact shared database and multiworker topology in staging; local CAS/unit tests do not prove production locking or load.

The root Compose stack runs `shrecknet_auth_session_cleanup` continuously, invoking this session cleanup once per minute off the renewal hot path. For non-Compose deployments, schedule the command at least every minute from the `shrecknet/` package:

```sh
PYTHONPATH=. python scripts/cleanup_auth_sessions.py
```

It clears replay ciphertext older than 30 seconds, removes expired counters, and deletes rotation/session rows after absolute expiry. Logical replay validity is always 30 seconds; physical cleanup timing depends on the schedule and is not promised to be exactly 30 seconds. Do not run unrelated table-destructive migrations or drop session tables during rollback.

New/changed passwords use Argon2id (19 MiB, two iterations, one lane). Legacy SHA-256 hashes remain verifiable and are upgraded after successful eligible password authentication with constant-time legacy comparison. Password hashing runs off the async event loop. Existing API registration minimums are unchanged; the new browser registration minimum is 12. Rollback must retain Argon2id verification and additive schema: an older SHA-only release cannot verify upgraded accounts. Removing browser UI must never restore password persistence as a migration strategy.

Rotate the Fernet secret only as an operator-controlled change: existing CSRF/retry ciphertext becomes invalid, so pending retries may need a fresh login. Current opaque refresh credentials are digest-based. Provision identical secrets/keys before adding workers. Production release still requires HTTPS cookie checks, expiry/reconnect, two-tab/lost-response checks, cleanup monitoring, measured load and password cost, and revocation across deployed consumers.

## SDK and generated contracts

`python_sdk/shrecknet_client/browser_auth.py` exposes opt-in `BrowserAuthAPI(client, exact_origin)` with login, register, refresh, logout, verify, and resend methods, using the client's memory-only HTTPX cookie jar. It does not replace ordinary `Shrecknet.login()` or persist cookie/password state to disk. See [SDK auth guide](../../python_sdk/docs/getting_started/auth-and-bootstrap.md).

Export the actual new router schema without starting the full platform:

```sh
PYTHONPATH=. python scripts/export_browser_auth_openapi.py --output /path/to/shreckos/contracts/shrecknet-browser-auth.openapi.json
```

Focused tests are `shrecknet/tests/test_browser_auth.py`, `python_sdk/tests/test_browser_auth_sdk.py`, and `shreckworld/tests/test_browser_session_auth.py`, plus the RPG consumer's browser-auth tests in its repository. `shrecknet/tests/browser_auth_server.py` is a temporary loopback-only real-route integration harness, not a production entry point.
