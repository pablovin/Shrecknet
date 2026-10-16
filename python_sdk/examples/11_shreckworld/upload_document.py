"""Upload with autofill using the independent ShreckWorld HTTP API.

Run: python upload_document.py SOURCE --world-id WORLD_ID
Set SHRECKNET_TOKEN and optionally SHRECKWORLD_API_URL (default localhost:8121).
Requires httpx, already a Shrecknet SDK dependency.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--title", help="Optional manual title overriding document metadata")
    args = parser.parse_args()
    fields = {"auto_extract_metadata": "true"}
    if args.title:
        fields["title"] = args.title
    with httpx.Client(
        base_url=os.environ.get("SHRECKWORLD_API_URL", "http://localhost:8121"),
        headers={"Authorization": f"Bearer {os.environ['SHRECKNET_TOKEN']}"},
        timeout=120,
    ) as client, args.source.open("rb") as source:
        response = client.post(
            f"/admin/shreckworlds/{args.world_id}/library-items",
            data=fields, files={"pdf": (args.source.name, source)},
        )
        response.raise_for_status()
        item = response.json()
        print(item)
        if item["cover_url"]:
            cover = client.get(item["cover_url"])
            cover.raise_for_status()
            output = args.source.with_name(f"{args.source.stem}-cover.png")
            output.write_bytes(cover.content)
            print(f"Cover saved to {output}")


if __name__ == "__main__":
    main()
