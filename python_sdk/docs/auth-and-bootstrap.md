# Auth and Bootstrap

Shrecknet bootstrap rule: the first registered user becomes admin.

## Canonical helper

Use [0_user_registration.py](../../python_sdk/examples/01_login_and_user_creation/00_user_registration.py) for reusable bootstrap+login logic.

The helper does:
1. `GET /users/bootstrap` to check whether users exist.
2. If empty DB: `POST /users/` as admin.
3. Otherwise: attempts regular registration and ignores user/email conflict.
4. Calls login and stores token in SDK client.

## Canonical registration-first example

Use [01_register_and_login.py](../../python_sdk/examples/01_login_and_user_creation/02_register_and_login.py) before other workflows.

## Deprecated naming note

`_bootstrap.py` was used earlier and is now deprecated/removed. `0_user_registration.py` is the canonical bootstrap helper.
