"""Shared deterministic Elder retrieval entry point; no conversational synthesis."""
from app.jobs.elder.executor import ElderRetrievalExecutor


async def retrieve_plan(retriever, **kwargs):
    return await ElderRetrievalExecutor(retriever).execute(**kwargs)
