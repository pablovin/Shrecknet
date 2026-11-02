"""Built-in Elder calls; configure sdk authentication as in the SDK quickstart."""
from shrecknet_client.models import ElderQueryRequest, ElderWorldScope


async def compare_worlds(sdk, query: str, world_ids: list[int]):
    return await sdk.elder.query_site(
        ElderQueryRequest(
            query=query,
            world_scope=ElderWorldScope(mode="selected", ontology_ids=world_ids),
        )
    )


async def ask_all_worlds(sdk, query: str):
    return await sdk.elder.query_site(
        ElderQueryRequest(query=query, world_scope=ElderWorldScope(mode="all"))
    )
