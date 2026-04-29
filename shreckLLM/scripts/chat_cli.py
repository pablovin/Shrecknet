from __future__ import annotations

import argparse
import asyncio
import uuid
from typing import Any

import httpx


def _pick_provider(default_provider_id: str, providers: dict[str, Any]) -> str:
    ids = sorted([str(k) for k in providers.keys()])
    if not ids:
        print("No providers returned by API.")
        return default_provider_id
    print("Providers:")
    for idx, provider_id in enumerate(ids, start=1):
        marker = " (default)" if provider_id == default_provider_id else ""
        print(f"  {idx}. {provider_id}{marker}")
    while True:
        raw = input(f"Select provider [1-{len(ids)}] (Enter=default): ").strip()
        if not raw:
            return default_provider_id or ids[0]
        if raw.isdigit():
            pos = int(raw)
            if 1 <= pos <= len(ids):
                return ids[pos - 1]
        if raw in providers:
            return raw
        print("Invalid selection. Try again.")


def _pick_model(default_model: str, models: list[str], provider_id: str) -> str:
    if not models:
        typed = input(
            f"No catalog models returned for provider '{provider_id}'. Enter model id (Enter={default_model}): "
        ).strip()
        return typed or default_model

    print(f"Available models for provider '{provider_id}':")
    for idx, model in enumerate(models, start=1):
        marker = " (default)" if model == default_model else ""
        print(f"  {idx}. {model}{marker}")

    while True:
        raw = input(f"Select model [1-{len(models)}] (Enter=default {default_model}): ").strip()
        if not raw:
            return default_model or models[0]
        if raw.isdigit():
            pos = int(raw)
            if 1 <= pos <= len(models):
                return models[pos - 1]
        if raw:
            return raw
        print("Invalid selection. Try again.")


def _build_config_patch(args: argparse.Namespace) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if args.set_default_provider_id is not None:
        patch["default_provider_id"] = args.set_default_provider_id
    if args.set_provider_default_model:
        # format: provider_id=model
        item = str(args.set_provider_default_model)
        if "=" in item:
            provider_id, model = item.split("=", 1)
            provider_id = provider_id.strip()
            model = model.strip()
            if provider_id and model:
                patch["provider_defaults"] = {provider_id: {"default_model": model}}
    return patch


async def _load_models(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/models")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return {}
    return payload


async def _apply_config_if_requested(
    client: httpx.AsyncClient,
    *,
    admin_token: str | None,
    patch: dict[str, Any],
) -> bool:
    if not patch:
        return True
    if not admin_token:
        print("config> --admin-token is required when using --set-* flags")
        return False

    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.put("/config", json=patch, headers=headers)
    if response.status_code != 200:
        print(f"config> update failed: HTTP {response.status_code}: {response.text}")
        return False

    print(f"config> updated keys: {', '.join(sorted(patch.keys()))}")
    return True


async def _show_config_if_requested(client: httpx.AsyncClient, admin_token: str | None, show: bool) -> None:
    if not show:
        return
    if not admin_token:
        print("config> skipping show config: --admin-token not provided")
        return

    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.get("/config", headers=headers)
    if response.status_code != 200:
        print(f"config> read failed: HTTP {response.status_code}: {response.text}")
        return
    cfg = response.json()
    print("config> current runtime config:")
    print(
        {
            "default_provider_id": cfg.get("default_provider_id"),
            "providers": list((cfg.get("provider_defaults") or {}).keys()),
        }
    )


async def run(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        patch = _build_config_patch(args)
        if not await _apply_config_if_requested(client, admin_token=args.admin_token, patch=patch):
            return 1
        await _show_config_if_requested(client, admin_token=args.admin_token, show=args.show_config)

        ready = await client.get("/ready")
        ready.raise_for_status()
        ready_payload = ready.json()
        if not ready_payload.get("ready"):
            print(f"Service not ready: {ready_payload}")
            return 1

        models_payload = await _load_models(client)
        default_provider_id = str(models_payload.get("default_provider_id") or "")
        providers = models_payload.get("providers") if isinstance(models_payload.get("providers"), dict) else {}

        selected_provider_id = args.provider_id or _pick_provider(default_provider_id, providers)

        provider_payload = providers.get(selected_provider_id) if isinstance(providers, dict) else {}
        if not isinstance(provider_payload, dict):
            provider_payload = {}

        default_model = str(provider_payload.get("default_model") or "")
        model_list = provider_payload.get("models") if isinstance(provider_payload.get("models"), list) else []
        cleaned_models = [str(item) for item in model_list if isinstance(item, str)]

        selected_model = args.model or _pick_model(default_model, cleaned_models, selected_provider_id)

        conv_id = args.conversation_id or f"cli-{uuid.uuid4().hex[:10]}"
        print(f"Using provider_id: {selected_provider_id}")
        print(f"Using model: {selected_model}")
        print(f"Conversation id: {conv_id}")
        print("Type /exit to quit.\n")

        while True:
            user_text = input("you> ").strip()
            if not user_text:
                continue
            if user_text.lower() in {"/exit", "exit", "quit"}:
                print("bye")
                return 0

            payload = {
                "provider_id": selected_provider_id,
                "model": selected_model,
                "messages": [{"role": "user", "content": user_text}],
                "conversation_id": conv_id,
                "use_conversation_memory": True,
            }
            response = await client.post("/chat", json=payload)
            if response.status_code != 200:
                print(f"error> HTTP {response.status_code}: {response.text}")
                continue
            data = response.json()
            text = str(data.get("text") or "")
            latency_ms = data.get("latency_ms")
            actual_provider = data.get("provider_id")
            actual_model = data.get("resolved_model")
            print(f"bot[{actual_provider}:{actual_model}]> {text}")
            if latency_ms is not None:
                print(f"     ({latency_ms} ms)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive shreckLLM chat CLI")
    parser.add_argument("--base-url", default="http://localhost:8110")
    parser.add_argument("--conversation-id", default=None)

    parser.add_argument("--provider-id", default=None)
    parser.add_argument("--model", default=None)

    parser.add_argument("--admin-token", default=None, help="Shrecknet admin/world_builder bearer token")
    parser.add_argument("--show-config", action="store_true")
    parser.add_argument("--set-default-provider-id", default=None)
    parser.add_argument("--set-provider-default-model", default=None, help="format: provider_id=model")

    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
