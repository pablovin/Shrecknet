import asyncio
import os
import uuid
from importlib import import_module

from shrecknet_client import AgentCreate, AgentUpdate, Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Demonstrate complete agent management lifecycle.
# Expected result:
# - Lists job types, creates/updates agent, links ontology, and deletes agent.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # Discover supported backend job types for agents.
        jobs = await sdk.agents.available_jobs()
        print("available_jobs:", jobs)
        print("job_type_notes: elder=conversation/retrieval; architect=lore structure; librarian=library intelligence; novelist=draft writing")

        # Create a fresh sample agent.
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

        # Update mutable fields.
        updated = await sdk.agents.update(
            created.id,
            AgentUpdate(description="Updated by SDK example", active=True),
        )
        print("updated:", updated.model_dump())

        # Confirm list endpoint includes this agent.
        all_agents = await sdk.agents.list(limit=20)
        print("agents_count:", len(all_agents))

        # Attach/detach first ontology to demonstrate relation management.
        ontologies = await sdk.ontologies.list(limit=1)
        if ontologies:
            attached = await sdk.agents.attach_ontology(created.id, ontologies[0].id)
            print("attached_ontology_ids:", attached.ontology_ids)
            detached = await sdk.agents.detach_ontology(created.id, ontologies[0].id)
            print("detached_ontology_ids:", detached.ontology_ids)

        # Cleanup keeps reruns safe.
        await sdk.agents.delete(created.id)
        print("deleted_agent:", created.id)


if __name__ == "__main__":
    asyncio.run(main())
