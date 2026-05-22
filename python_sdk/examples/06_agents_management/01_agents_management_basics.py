import asyncio
import os
import uuid

from shrecknet_client import AgentCreate, AgentUpdate, Shrecknet

from importlib import import_module

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login



# Step 1: connect to Shrecknet URLs from environment.
# Step 2: ensure user bootstrap/login (first user becomes admin).
# Step 3: execute domain workflow actions and print results.

async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        jobs = await sdk.agents.available_jobs()
        print("available_jobs:", jobs)
        print("job_type_notes: elder=conversation/retrieval; architect=lore structure; librarian=library intelligence; novelist=draft writing")

        name = f"sdk-agent-{uuid.uuid4().hex[:8]}"
        created = await sdk.agents.create(
            AgentCreate(
                name=name,
                description="SDK example agent",
                writing_style="Clear, direct, grounded in context",
                job="elder",
                active=True,
                ontology_ids=[],
            )
        )
        print("created:", created.model_dump())

        updated = await sdk.agents.update(
            created.id,
            AgentUpdate(description="Updated by SDK example", active=True),
        )
        print("updated:", updated.model_dump())

        all_agents = await sdk.agents.list(limit=20)
        print("agents_count:", len(all_agents))

        ontologies = await sdk.ontologies.list(limit=1)
        if ontologies:
            attached = await sdk.agents.attach_ontology(created.id, ontologies[0].id)
            print("attached_ontology_ids:", attached.ontology_ids)
            detached = await sdk.agents.detach_ontology(created.id, ontologies[0].id)
            print("detached_ontology_ids:", detached.ontology_ids)

        await sdk.agents.delete(created.id)
        print("deleted_agent:", created.id)


if __name__ == "__main__":
    asyncio.run(main())
