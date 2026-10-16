# Read public roster profiles

Use the authenticated client to resolve up to 100 display profiles in one request:

```python
from shrecknet_client.client import AsyncShrecknetClient

async def roster(base_url: str, access_token: str):
    async with AsyncShrecknetClient(base_url, token=access_token) as client:
        profiles = await client.user_summaries([1, 2])
        return {profile.id: (profile.full_name or profile.username, profile.avatar_url)
                for profile in profiles}
```

The SDK validates 1–100 positive integer IDs and returns `PublicUserSummary` records. Duplicate IDs collapse server-side and missing users are omitted. The result contains no private account fields. Batch larger rosters explicitly and retain missing-member handling in the application. Existing bearer authentication/error mapping applies. See the [HTTP contract](../../../Documentation/API/PUBLIC_USER_SUMMARIES.md).
