import asyncio
import os
from importlib import import_module

from shrecknet_client import ElderChatCreate, ElderChatUpdate, ElderQueryRequest, Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Demonstrate full Elder query lifecycle: preflight, chat management, query, and chat-file retrieval.
# Expected result:
# - Prints readiness report, chat lifecycle states, and Elder response summary.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    agent_id = os.getenv("SHRECKNET_ELDER_AGENT_ID")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))
    query = os.getenv("SHRECKNET_ELDER_QUERY", "What changed recently in this world?")

    if not agent_id:
        raise RuntimeError("SHRECKNET_ELDER_AGENT_ID is required")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        preflight = await sdk.elder.preflight(agent_id=agent_id, ontology_id=ontology_id, strict=False)
        print("preflight:", preflight.model_dump())

        chat = await sdk.elder.create_chat(ElderChatCreate(agent_id=agent_id, name="SDK Elder Session", color="#2D7FF9"))
        print("chat_created:", chat.model_dump())

        listed = await sdk.elder.list_chats(agent_id=agent_id, limit=20)
        print("chats_total:", listed.total)

        chat_full = await sdk.elder.get_chat(chat.id, include_history=True)
        print("chat_history_initial_count:", len(chat_full.history))

        renamed = await sdk.elder.update_chat(chat.id, ElderChatUpdate(name="SDK Elder Session Updated"))
        print("chat_updated:", renamed.model_dump())

        response = await sdk.elder.query(
            agent_id,
            ElderQueryRequest(query=query, chat_id=chat.id, include_trace=False),
        )
        print("elder_answer:", response.answer)
        print("sources_count:", len(response.sources))

        chat_file = await sdk.elder.get_chat_file(chat.id)
        print("chat_file_keys:", list(chat_file.keys()))

        await sdk.elder.delete_chat(chat.id)
        print("chat_deleted:", chat.id)


if __name__ == "__main__":
    asyncio.run(main())
