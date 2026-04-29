from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any

import httpx


async def _post_chat(client: httpx.AsyncClient, payload: dict[str, Any]) -> tuple[bool, float, str]:
    started = time.monotonic()
    response = await client.post("/chat", json=payload)
    elapsed_ms = (time.monotonic() - started) * 1000
    if response.status_code != 200:
        return False, elapsed_ms, f"HTTP {response.status_code}: {response.text}"
    data = response.json()
    text = str(data.get("text") or "")
    return True, elapsed_ms, text


def _build_config_patch(args: argparse.Namespace) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if args.set_default_provider_id is not None:
        patch["default_provider_id"] = args.set_default_provider_id
    if args.set_request_timeout is not None:
        patch["request_timeout_seconds"] = args.set_request_timeout
    if args.set_max_queue_wait is not None:
        patch["max_queue_wait_seconds"] = args.set_max_queue_wait
    return patch


async def _apply_config_if_requested(
    client: httpx.AsyncClient,
    *,
    admin_token: str | None,
    patch: dict[str, Any],
) -> bool:
    if not patch:
        return True

    if not admin_token:
        print("[FAIL] config patch requested but --admin-token is missing")
        return False

    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.put("/config", json=patch, headers=headers)
    if response.status_code != 200:
        print(f"[FAIL] /config update failed: HTTP {response.status_code} {response.text}")
        return False

    verify = await client.get("/config", headers=headers)
    if verify.status_code != 200:
        print(f"[FAIL] /config readback failed: HTTP {verify.status_code} {verify.text}")
        return False

    cfg = verify.json()
    print("[PASS] /config updated")
    print(f"       applied keys: {', '.join(sorted(patch.keys()))}")
    print(
        "       snapshot:",
        {
            "default_provider_id": cfg.get("default_provider_id"),
            "providers": list((cfg.get("provider_defaults") or {}).keys()),
        },
    )
    return True


async def run(
    base_url: str,
    conversation_id: str,
    parallel: int,
    provider_id: str,
    admin_token: str | None,
    config_patch: dict[str, Any],
) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        if not await _apply_config_if_requested(client, admin_token=admin_token, patch=config_patch):
            return 1

        ready = await client.get("/ready")
        if ready.status_code != 200:
            print(f"[FAIL] /ready returned {ready.status_code}")
            return 1
        ready_payload = ready.json()
        if not ready_payload.get("ready"):
            print(f"[FAIL] /ready indicates not ready: {ready_payload}")
            return 1
        print("[PASS] /ready")

        turn1_payload = {
            "provider_id": provider_id,
            "messages": [{"role": "user", "content": "My favorite color is blue. Remember that."}],
            "conversation_id": conversation_id,
            "use_conversation_memory": True,
        }
        ok1, t1, text1 = await _post_chat(client, turn1_payload)
        if not ok1:
            print(f"[FAIL] turn1 failed in {t1:.2f}ms: {text1}")
            return 1
        print(f"[PASS] turn1 {t1:.2f}ms")

        turn2_payload = {
            "provider_id": provider_id,
            "messages": [{"role": "user", "content": "What is my favorite color?"}],
            "conversation_id": conversation_id,
            "use_conversation_memory": True,
        }
        ok2, t2, text2 = await _post_chat(client, turn2_payload)
        if not ok2:
            print(f"[FAIL] turn2 failed in {t2:.2f}ms: {text2}")
            return 1
        print(f"[PASS] turn2 {t2:.2f}ms")

        if "blue" not in text2.lower():
            print(f"[FAIL] memory continuity check failed: response='{text2}'")
            return 1
        print("[PASS] memory continuity")

        async def _parallel_one(i: int):
            payload = {
                "provider_id": provider_id,
                "messages": [{"role": "user", "content": f"Say pong {i}"}],
                "conversation_id": f"{conversation_id}-p{i}",
                "use_conversation_memory": True,
            }
            return await _post_chat(client, payload)

        burst = await asyncio.gather(*(_parallel_one(i) for i in range(parallel)))
        oks = [item[0] for item in burst]
        lats = [item[1] for item in burst]

        if not all(oks):
            print("[FAIL] parallel burst had failures")
            for idx, (ok, lat, text) in enumerate(burst):
                if not ok:
                    print(f"  - request {idx}: {lat:.2f}ms {text}")
            return 1

        print(
            "[PASS] parallel burst",
            f"count={parallel}",
            f"mean_ms={statistics.mean(lats):.2f}",
            f"p95_ms={sorted(lats)[max(0, int(0.95 * len(lats)) - 1)]:.2f}",
        )

        print("[PASS] smoke test complete")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="shreckLLM end-to-end chat smoke test")
    parser.add_argument("--base-url", default="http://localhost:8110")
    parser.add_argument("--conversation-id", default="smoke-conversation")
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--provider-id", required=True, choices=["ollama", "openai"])

    parser.add_argument("--admin-token", default=None, help="Shrecknet admin/world_builder bearer token")
    parser.add_argument("--set-default-provider-id", default=None)
    parser.add_argument("--set-request-timeout", default=None, type=float)
    parser.add_argument("--set-max-queue-wait", default=None, type=float)

    args = parser.parse_args()
    patch = _build_config_patch(args)
    return asyncio.run(
        run(
            args.base_url,
            args.conversation_id,
            args.parallel,
            args.provider_id,
            args.admin_token,
            patch,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
