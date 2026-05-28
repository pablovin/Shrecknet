import asyncio
import os

from shrecknet_client import NovelistRunCreate, OntologyInstanceCreate, OntologyInstanceEntity, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, env, load_state, save_state


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        agent_id = env("SHRECKNET_NOVELIST_AGENT_ID", required=True)
        pdf_path = env("SHRECKNET_PDF_PATH", required=True)
        ontology_id = int(state["ontology_id"])
        adventure_id = state["adventure_instance_id"]

        run = await sdk.novelist.start_run_from_upload(agent_id, pdf_path=pdf_path)
        await sdk.novelist.wait_for_run(run.id, timeout_s=1200)
        run = await sdk.novelist.get_run(run.id)

        draft = run.draft_text or run.request_payload.get("unstructured_text", "") if run.request_payload else ""
        story = await sdk.ontology_instances.create(
            OntologyInstanceCreate(
                ontology_id=ontology_id,
                name="Story from Novelist Draft",
                entities=[
                    OntologyInstanceEntity(
                        definition_id=1,
                        alias="Story from Novelist Draft",
                        text=f"Adventure: {adventure_id}\n\n{draft[:5000]}",
                        author_type="ai",
                        author_id="novelist",
                    )
                ],
            )
        )

        state["novelist_run_id"] = run.id
        state["story_instance_id"] = story.id
        save_state(state)
        print("novelist_run_id:", run.id)
        print("story_instance_id:", story.id)


if __name__ == "__main__":
    asyncio.run(main())
