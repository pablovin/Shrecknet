import asyncio
import os

from shrecknet_client import ArchitectAnalysisRequest, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, env, load_state, save_state


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        agent_id = env("SHRECKNET_ARCHITECT_AGENT_ID", required=True)
        story_id = state["story_instance_id"]

        run = await sdk.architect.analyze(agent_id, ArchitectAnalysisRequest(ontology_instance_id=story_id))
        await sdk.architect.wait_for_analysis(run.id, timeout_s=1200)
        run = await sdk.architect.get_run(run.id)
        state["architect_run_id"] = run.id
        save_state(state)
        print("architect_run_id:", run.id)
        print("proposals:", len(run.proposals))


if __name__ == "__main__":
    asyncio.run(main())
