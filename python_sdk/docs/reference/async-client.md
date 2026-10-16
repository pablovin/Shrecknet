# AsyncShrecknetClient

Low-level async HTTP client for Shrecknet APIs.

## Methods

### `user_summaries(self, ids)`

Resolve 1–100 positive user IDs into typed `PublicUserSummary` records (ID, username, full name, and optional avatar). Duplicates collapse and missing users are omitted. See [the workflow](../core_workflows/public-user-summaries.md).

### `set_token(self, token)`

Set bearer token used for authenticated requests.

### `clear_token(self)`

Clear bearer token for subsequent requests.

### `raw_request(self, method, path)`

Execute a raw HTTP request and map Shrecknet errors to SDK exceptions.

### `bootstrap_status(self)`

Return whether at least one user already exists in Shrecknet.

### `register_user(self)`

Register a user through `/users/` for bootstrap or standard onboarding.

### `login(self, username_or_email, password)`

Authenticate with username/email and store returned bearer token.

### `me(self)`

Fetch profile of the current authenticated user.
