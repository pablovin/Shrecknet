# Shrecknet

High-level SDK facade exposing domain APIs and shared auth state.

## Methods

### `login(self, username_or_email, password)`

Authenticate and store bearer token on the underlying async client.

### `me(self)`

Return current authenticated user profile from `/users/me`.

### `raw_request(self, method, path)`

Raw escape hatch for uncovered endpoints.

### `set_token(self, token)`

Set bearer token manually.

### `clear_token(self)`

Clear bearer token from current client state.
