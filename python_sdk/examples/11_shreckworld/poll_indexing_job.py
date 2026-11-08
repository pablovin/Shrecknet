"""Poll one ShreckWorld indexing attempt without starting/restarting a job.

Set SHRECKNET_TOKEN and optionally SHRECKWORLD_API_URL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx


def print_timing_summary(job: dict) -> None:
    """Show terminal measurements separately from the machine-readable JSON."""
    print(f"Indexing {job['status']} — timings in seconds", file=sys.stderr)
    for key in ("elapsed_seconds", "queue_wait_seconds", "processing_seconds"):
        value = job.get(key)
        if value is not None:
            print(f"  {key}: {value:.3f}", file=sys.stderr)
    metrics = job.get("metrics") or {}
    for group in ("phase_seconds", "parser_seconds"):
        values = metrics.get(group) or {}
        if values:
            print(f"  {group}:", file=sys.stderr)
            for name, seconds in sorted(values.items(), key=lambda item: item[1], reverse=True):
                print(f"    {name}: {seconds:.3f}", file=sys.stderr)
    if metrics.get("parser_seconds"):
        print("  Parser substeps are included in parsing; do not add them to the phase total.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--timeout", type=float, default=600, help="Polling deadline in seconds")
    parser.add_argument("--result-file", type=Path, help="Save the terminal job JSON for timing comparisons")
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
            if job["status"] in {"failed", "completed"}:
                print_timing_summary(job)
            if job["status"] in {"failed", "completed"} and args.result_file:
                args.result_file.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
            if job["status"] == "failed":
                raise SystemExit(job["detail"] or "Indexing failed")
            if job["status"] == "completed":
                return
            time.sleep(1.5)
    raise SystemExit("Polling deadline reached; the remote job has not been cancelled")


if __name__ == "__main__":
    main()
