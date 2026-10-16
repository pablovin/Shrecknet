# Public user profile summaries

`GET /contracts/users?ids=1&ids=2` resolves public display profiles for application rosters without one HTTP request per member. Shrecknet owns these profiles; ShreckRPG consumes them for authorized game-member lists.

The route requires the same authenticated user dependency as `/contracts/users/{user_id}`. Supply 1–100 positive integer IDs as repeated `ids` parameters. Invalid/missing IDs or excessive list length produce 422; unauthenticated requests produce 401. Duplicates are collapsed, missing users are omitted, and results are ordered by numeric ID. The service performs one database read for the batch. This route applies the existing authenticated profile-read policy; it does not independently grant game/world membership or authorize content reads.

Each result contains `id` (integer), `username`, `full_name`, and nullable `avatar_url`. Email, role, password, approval state, preferences, and entity associations are excluded. Avatars may be null; clients should use their normal image fallback. A successful empty result means none of the requested users exist, not that the consumer's roster is empty. The route has no writes, background jobs, private profile cache, or new configuration.

Schemas live in `app/schemas/user_contracts.py`, roster composition in the existing users service, and the batched database read in `app/repositories/user_profiles.py`. Existing single-user/me contracts are unchanged. No database migration is required. Deploy this additive read before switching ShreckRPG's roster adapter; rollback that adapter before removing this endpoint.

The Python SDK exposes `await client.user_summaries([1, 2])`, returning typed `PublicUserSummary` records. See [the SDK workflow](../../python_sdk/docs/core_workflows/public-user-summaries.md). Focused backend tests cover authentication, bounds, deduplication, missing IDs, privacy, and query count; the SDK test covers the request and typed response.
