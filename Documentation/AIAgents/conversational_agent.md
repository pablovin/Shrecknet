# Conversational Agent Flow

This diagram outlines the flow of a chat request through the backend conversational agent and highlights the primary classes involved.

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /agents/{id}/chat
    participant Chat as crud_agent_conversational.chat_with_agent
    participant Shrecknet as agentic_worker_shrecknet
    participant LLMWorker as agentic_worker_llm
    participant LLM as LangGraph\n(ChatPromptTemplate → ChatOpenAI)
    User->>API: POST message
    API->>Chat: chat_with_agent(messages)
    Chat->>Shrecknet: decompose_question(query)
    Shrecknet-->>Chat: sub-questions
    Chat->>Shrecknet: query_world_embeddings(sub-questions)
    Shrecknet-->>Chat: annotated results
    Chat->>LLMWorker: aggregate_prune_and_dedup(results)
    LLMWorker-->>Chat: context & sources
    Chat->>Chat: ensure_personality_prompts()
    Chat->>LLM: invoke graph
    LLM-->>Chat: answer
    Chat->>LLMWorker: validate_response(query, answer)
    LLMWorker-->>Chat: pass/fail
    alt needs retry
        Chat->>LLM: fallback prompt
        LLM-->>Chat: revised answer
    end
    Chat-->>API: answer + sources
    API-->>User: JSON response
```

* **crud_agent_conversational.chat_with_agent** orchestrates the conversation pipeline.
* **agentic_worker_shrecknet** resolves context from the world and **agentic_worker_llm** refines results and validates the model output.
* **ChatPromptTemplate**, **ChatOpenAI**, and **LangGraph** compose the LLM call.
