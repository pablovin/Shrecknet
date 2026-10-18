"""Submit and poll a ShreckWorld planned-evidence agent job."""
from __future__ import annotations

import argparse
import asyncio
import os

from shrecknet_client import Shrecknet


async def run(args: argparse.Namespace) -> None:
    token = os.environ.get("SHRECKNET_TOKEN")
    if not token:
        raise SystemExit("Set SHRECKNET_TOKEN to a current Shrecknet bearer token.")
    async with Shrecknet(token=token, shreckworld_base_url=args.base_url) as sdk:
        job = await sdk.shreckworld.start_agent_query(args.world_id, args.question)
        print(f"{job.id}: {job.status}/{job.stage}", flush=True)
        while job.status not in {"completed", "failed"}:
            await asyncio.sleep(args.interval)
            job = await sdk.shreckworld.agent_job(args.world_id, job.id)
            print(f"{job.id}: {job.status}/{job.stage}", flush=True)
        if job.status == "failed":
            raise SystemExit(job.error or "ShreckWorld agent query failed.")
        if job.result is None:
            raise RuntimeError("Completed ShreckWorld agent job has no result.")
        print(job.result.answer)
        print("Coverage:", job.result.evidence_coverage.status if job.result.evidence_coverage else "unknown")
        for citation in job.result.citations:
            print(citation.source_id, citation.title, "page", citation.page_number, citation.excerpt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8121")
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--interval", type=float, default=1.5)
    asyncio.run(run(parser.parse_args()))
