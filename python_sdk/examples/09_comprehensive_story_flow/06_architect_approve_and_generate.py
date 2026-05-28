import asyncio
import os

from shrecknet_client import ArchitectGenerationRequest, ArchitectProposalStatusUpdate, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, load_state, save_state


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        run_id = state["architect_run_id"]

        run = await sdk.architect.get_run(run_id)
        proposal_ids = [p.id for p in run.proposals]
        if proposal_ids:
            await sdk.architect.update_proposal_statuses(
                run_id,
                ArchitectProposalStatusUpdate(proposal_ids=proposal_ids, status="approved"),
            )

        reviewed_pipeline_output = {"proposals": [p.model_dump() for p in run.proposals], "run_id": run.id}
        await sdk.architect.generate(
            run.id,
            ArchitectGenerationRequest(
                run_id=run.id,
                reviewed_pipeline_output=reviewed_pipeline_output,
                author_type="user",
                author_id="sdk-comprehensive",
            ),
        )
        await sdk.architect.wait_for_generation(run.id, timeout_s=1200)

        state["architect_generated"] = True
        save_state(state)
        print("architect_generation_done_for_run:", run.id)


if __name__ == "__main__":
    asyncio.run(main())
