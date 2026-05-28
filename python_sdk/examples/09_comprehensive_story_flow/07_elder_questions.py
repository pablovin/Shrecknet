import asyncio
import os

from shrecknet_client import ElderChatCreate, ElderQueryRequest, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, env, load_state, save_state


QUESTIONS = [
    "Summarize the key events in the new story.",
    "Who are the most important characters and why?",
    "What unresolved risks should the party address next?",
]


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        agent_id = env("SHRECKNET_ELDER_AGENT_ID", required=True)
        ontology_id = int(state["ontology_id"])

        preflight = await sdk.elder.preflight(agent_id=agent_id, ontology_id=ontology_id, strict=False)
        print("elder_preflight_ready:", preflight.ready)

        chat = await sdk.elder.create_chat(ElderChatCreate(agent_id=agent_id, name="Comprehensive Story QA"))
        for q in QUESTIONS:
            res = await sdk.elder.query(agent_id, ElderQueryRequest(query=q, chat_id=chat.id))
            print("question:", q)
            print("answer:", res.answer)
            print("sources_count:", len(res.sources))

        state["elder_chat_id"] = chat.id
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
