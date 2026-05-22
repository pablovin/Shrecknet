import asyncio
import os
from importlib import import_module

from shrecknet_client import ArchitectGenerationRequest, Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Trigger Architect generation from a curated run and wait for completion.
# Expected result:
# - Starts generation, waits for generation job terminal state, then prints final run reconciliation summary.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    run_id = os.getenv("SHRECKNET_ARCHITECT_RUN_ID")

    if not run_id:
        raise RuntimeError("SHRECKNET_ARCHITECT_RUN_ID is required")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # 1) Load run to discover agent + current review state.
        run = await sdk.architect.get_run(run_id)
        print("run_loaded:", {"run_id": run.id, "status": run.status, "agent_id": run.agent_id})

        # 2) Build reviewed payload from current proposals (backend remains source of truth).
        reviewed_pipeline_output = {
            "proposals": [p.model_dump() for p in run.proposals],
            "run_id": run.id,
        }

        # 3) Trigger generation step from curated proposals.
        req = ArchitectGenerationRequest(
            run_id=run.id,
            reviewed_pipeline_output=reviewed_pipeline_output,
            author_type="user",
            author_id="sdk-example",
        )
        run_after_trigger = await sdk.architect.generate(run.id, req)
        print(
            "generation_triggered:",
            {
                "run_id": run_after_trigger.id,
                "generation_job_id": run_after_trigger.generation_job_id,
                "status": run_after_trigger.status,
            },
        )

        # 4) Wait for generation background job to complete.
        job = await sdk.architect.wait_for_generation(run.id, timeout_s=900)
        print("generation_job_final:", job.model_dump())

        # 5) Fetch final run snapshot and print reconciliation-related identifiers.
        final_run = await sdk.architect.get_run(run.id)
        print(
            "final_run_summary:",
            {
                "run_id": final_run.id,
                "status": final_run.status,
                "background_job_id": final_run.background_job_id,
                "generation_job_id": final_run.generation_job_id,
                "proposal_count": len(final_run.proposals),
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
