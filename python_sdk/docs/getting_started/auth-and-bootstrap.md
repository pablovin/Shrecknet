# Auth and Bootstrap

Shrecknet bootstrap rule: the first registered user becomes admin.

## Canonical helper

Use [0_user_registration.py](../../../python_sdk/examples/01_login_and_user_creation/00_user_registration.py) for reusable bootstrap+login logic.

The helper does:
1. `GET /users/bootstrap` to check whether users exist.
2. If empty DB: `POST /users/` as admin.
3. Otherwise: attempts regular registration and ignores user/email conflict.
4. Calls login and stores token in SDK client.

## Canonical registration-first example

Use [01_register_and_login.py](../../../python_sdk/examples/01_login_and_user_creation/02_register_and_login.py) before other workflows.

## Deprecated naming note

`_bootstrap.py` was used earlier and is now deprecated/removed. `0_user_registration.py` is the canonical bootstrap helper.


## Opt-in browser sessions

Legacy `Shrecknet.login()` remains bearer-based. For an enabled browser-session deployment, use `BrowserAuthAPI(sdk.client, "https://os.example.com")`. It uses the HTTPX client's in-memory cookie jar and explicit Origin/CSRF headers. Call `login(identifier, password, remember_me=False)`, `register(...)`, `refresh(request_id=None)`, or `logout()`; verify/resend helpers are also available. A successful login/registration/renewal updates the shared client bearer token, while logout clears it and revokes the cookie session. Nothing is saved to disk.

Reuse a refresh request ID only to recover the same lost response within 30 seconds. The SDK does not automatically retry passwords or implement browser-tab coordination. An unknown/expired session needs a new login. New browser registration requires a 12-character password and never bootstraps the first admin; approval/verification outcomes must be handled explicitly.

See the [canonical browser API contract](../../../Documentation/API/BROWSER_AUTHENTICATION.md) for private deployment settings, origin/cookie rules, rate limits, errors, revocation, and migration.
