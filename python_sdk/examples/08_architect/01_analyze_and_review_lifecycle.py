import asyncio
import os
from importlib import import_module

from shrecknet_client import ArchitectAnalysisRequest, ArchitectProposalUpdate, Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Run Architect analysis and perform proposal curation operations.
# Expected result:
# - Creates an analysis run, waits for completion, then updates proposal statuses and patches one proposal.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    agent_id = os.getenv("SHRECKNET_ARCHITECT_AGENT_ID")
    ontology_instance_id = os.getenv("SHRECKNET_ONTOLOGY_INSTANCE_ID")

    if not agent_id:
        raise RuntimeError("SHRECKNET_ARCHITECT_AGENT_ID is required")
    if not ontology_instance_id:
        raise RuntimeError("SHRECKNET_ONTOLOGY_INSTANCE_ID is required")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # 1) Validate operational readiness: shreckLLM/providers + architect agent active/job type.
        preflight = await sdk.architect.preflight(agent_id=agent_id, strict=False)
        print("preflight:", preflight.model_dump())
        if not preflight.ready:
            return

        # 2) Trigger analysis run from one ontology instance.
        run = await sdk.architect.analyze(
            agent_id,
            ArchitectAnalysisRequest(ontology_instance_id=ontology_instance_id, max_chunks=80, chunk_size=1200),
        )
        print("run_created:", {"run_id": run.id, "status": run.status})

        # 3) Wait until analysis background job reaches terminal state.
        job = await sdk.architect.wait_for_analysis(run.id, timeout_s=900)
        print("analysis_job_final:", job.model_dump())

        # 4) Reload run and inspect proposals produced by analysis.
        run = await sdk.architect.get_run(run.id)
        print("proposals_count:", len(run.proposals))

        if not run.proposals:
            print("No proposals returned by Architect for this run.")
            return

        # 5) Mark first proposals as approved to simulate human curation.
        to_approve = [p.id for p in run.proposals[: min(2, len(run.proposals))]]
        updated = await sdk.architect.update_proposal_statuses(run.id, to_approve, "approved")
        print("status_update_total:", len(updated))

        # 6) Apply a targeted patch to one proposal (example: add metadata note).
        first = run.proposals[0]
        patched = await sdk.architect.patch_proposal(
            run.id,
            first.id,
            ArchitectProposalUpdate(metadata={"review_note": "Reviewed via SDK example"}),
        )
        print("patched_proposal:", {"id": patched.id, "status": patched.status})


if __name__ == "__main__":
    asyncio.run(main())
