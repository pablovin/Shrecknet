import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Validate and operate the full Elder embedding lifecycle (ontology + graphrag endpoints).
# Expected result:
# - Prints stats, runs embeddings, and prints final lifecycle report.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))
    node_id = os.getenv("SHRECKNET_NODE_ID")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        before = await sdk.embeddings.stats(ontology_id)
        print("before:", before.model_dump())

        handle = await sdk.embeddings.trigger(ontology_id)
        print("triggered_job:", {"job_id": handle.job_id, "status": handle.status, "type": handle.job_type})
        if handle.job_id > 0:
            final = await handle.wait(timeout_s=600)
            print("job_final:", final.model_dump())

        print("ensure_index:", (await sdk.embeddings.ensure_index()).model_dump())
        print("embed_ontology:", (await sdk.embeddings.embed_ontology(ontology_id, batch_size=25)).model_dump())
        print("backfill_chunks:", (await sdk.embeddings.backfill_chunks(ontology_id, batch_size=25)).model_dump())

        if node_id:
            print("embed_node:", (await sdk.embeddings.embed_node(node_id=node_id, ontology_id=ontology_id)).model_dump())

        # Optional destructive reset (disabled by default)
        if os.getenv("SHRECKNET_RESET_EMBEDDINGS", "0") == "1":
            print("reset_embeddings:", (await sdk.embeddings.reset_ontology_embeddings(ontology_id)).model_dump())

        report = await sdk.embeddings.lifecycle_report(ontology_id)
        print("lifecycle_report:", report.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
