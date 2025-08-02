# Conversational Agent Flow

This diagram outlines the flow of a chat request through the backend conversational agent, showing which functions and classes are invoked.

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /agents/{id}/chat
    participant Chat as chat_with_agent
    participant Workers as agentic_worker_* modules
    participant LLM as ChatOpenAI
    User->>API: POST message
    API->>Chat: chat_with_agent(messages)
    Chat->>Workers: decompose_question(query)
    Workers-->>Chat: sub-questions
    Chat->>Workers: query_world_embeddings(sub-questions)
    Workers-->>Chat: annotated results
    Chat->>Workers: aggregate_prune_and_dedup(results)
    Workers-->>Chat: context & sources
    Chat->>Chat: ensure_personality_prompts()
    Chat->>LLM: LangGraph Graph\n(ChatPromptTemplate + ChatOpenAI)
    LLM-->>Chat: answer
    Chat->>Workers: validate_response(query, answer)
    Workers-->>Chat: pass/fail
    alt needs retry
        Chat->>LLM: fallback prompt
        LLM-->>Chat: revised answer
    end
    Chat-->>API: answer + sources
    API-->>User: JSON response
```

* **chat_with_agent** orchestrates the conversation pipeline.
* **agentic_worker_llm** and **agentic_worker_shrecknet** modules provide `decompose_question`, `query_world_embeddings`, `aggregate_prune_and_dedup`, and `validate_response` helpers.
* **ChatPromptTemplate**, **ChatOpenAI**, and **LangGraph Graph** compose the LLM call.
