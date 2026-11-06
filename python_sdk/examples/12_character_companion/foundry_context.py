"""Send table background to an existing Companion chat; no live Foundry connection.

Set SHRECKNET_URL, SHRECKNET_TOKEN, SHRECKNET_CHAT_ID, SHRECKNET_WORLD_ID,
FOUNDRY_WORLD_ID, FOUNDRY_TABLE_TITLE and optionally FOUNDRY_TABLE_DESCRIPTION,
FOUNDRY_SYSTEM_ID, FOUNDRY_SYSTEM_TITLE, FOUNDRY_SYSTEM_VERSION.
The Foundry module normally collects these values directly, without a JSON export.
This example demonstrates the SDK transport with explicitly supplied metadata.
"""
import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

from shrecknet_client import Shrecknet


async def main():
    async with Shrecknet(os.environ["SHRECKNET_URL"], token=os.environ["SHRECKNET_TOKEN"]) as sdk:
        api = sdk.character_companion
        caps = await api.capabilities()
        if 1 not in caps.get("foundry_context", {}).get("versions", []):
            raise RuntimeError("Update the backend before sending Foundry context.")
        selection = await api.selection()
        chat_id = os.environ["SHRECKNET_CHAT_ID"]
        chat = await api.chat(chat_id)
        if selection.character_agent_id != chat["character_agent_id"]:
            raise RuntimeError("Select the chat's Companion before running this example.")
        snapshot = {
            "version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
            "table": {
                "foundry_world_id": os.environ["FOUNDRY_WORLD_ID"],
                "title": os.environ["FOUNDRY_TABLE_TITLE"],
                "description": os.getenv("FOUNDRY_TABLE_DESCRIPTION"),
                "foundry_version": "14",
                "system_id": os.getenv("FOUNDRY_SYSTEM_ID", ""),
                "system_title": os.getenv("FOUNDRY_SYSTEM_TITLE", ""),
                "system_version": os.getenv("FOUNDRY_SYSTEM_VERSION", ""),
                "shrecknet_world_id": os.environ["SHRECKNET_WORLD_ID"],
                "shrecknet_world_name": None,
            }, "documents": [],
        }
        # Retain both values if handling an ambiguous network failure in a caller.
        key = str(uuid4())
        turn = await api.send(chat_id, "What does this table background tell you?",
                              selection.version, idempotency_key=key, foundry_context=snapshot)
        async for event_name, event in api.events(turn.turn_id):
            if event_name == "response.final":
                print(event.payload["text"])
        print(await api.turn(turn.turn_id))


if __name__ == "__main__":
    asyncio.run(main())
