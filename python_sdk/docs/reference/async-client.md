# AsyncShrecknetClient

Low-level async HTTP client for Shrecknet APIs.

## Methods

### `user_summaries(self, ids)`

Read up to 100 public profiles; missing IDs are omitted by the service.

### `set_token(self, token)`

Set bearer token used for authenticated requests.

### `clear_token(self)`

Clear bearer token for subsequent requests.

### `raw_request(self, method, path, *, params=None, json=None, headers=None, files=None)`

Execute a raw HTTP request and map Shrecknet errors to SDK exceptions.

### `bootstrap_status(self)`

Return whether at least one user already exists in Shrecknet.

### `register_user(self, *, username, password, email, full_name='World Keeper', timezone='UTC', role='admin', entity_ids=None)`

Register a user through `/users/` for bootstrap or standard onboarding.

### `login(self, username_or_email, password)`

Authenticate with username/email and store returned bearer token.

### `me(self)`

Fetch profile of the current authenticated user.
