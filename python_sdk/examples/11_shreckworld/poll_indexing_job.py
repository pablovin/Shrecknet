"""Poll one ShreckWorld indexing attempt without starting/restarting a job.

Set SHRECKNET_TOKEN and optionally SHRECKWORLD_API_URL.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--timeout", type=float, default=600, help="Polling deadline in seconds")
    args = parser.parse_args()
    path = f"/admin/shreckworlds/{args.world_id}/library-items/{args.item_id}/index-jobs/{args.job_id}"
    deadline = time.monotonic() + args.timeout
    with httpx.Client(
        base_url=os.environ.get("SHRECKWORLD_API_URL", "http://localhost:8121"),
        headers={"Authorization": f"Bearer {os.environ['SHRECKNET_TOKEN']}"}, timeout=30,
    ) as client:
        while time.monotonic() < deadline:
            response = client.get(path)
            response.raise_for_status()
            job = response.json()
            print(json.dumps(job), flush=True)
            if job["status"] == "failed":
                raise SystemExit(job["detail"] or "Indexing failed")
            if job["status"] == "completed":
                return
            time.sleep(1.5)
    raise SystemExit("Polling deadline reached; the remote job has not been cancelled")


if __name__ == "__main__":
    main()
