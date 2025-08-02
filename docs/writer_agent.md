# Writer Agent Flow

These diagrams outline how the writer agent analyzes existing pages and generates new or updated pages within a world.

## Analyze Pages Job

```mermaid
sequenceDiagram
    participant User
    participant API as POST /agents/{id}/pages/{page_id}/analyze_job
    participant Celery as task_analyze_pages_job
    participant WriterCRUD as crud_agent_writer.analyze_pages
    participant Shrecknet as agentic_worker_shrecknet
    participant LLMWorker as agentic_worker_llm
    User->>API: request analysis
    API->>Celery: enqueue job + job.json
    Celery->>WriterCRUD: analyze_pages(agent, pages)
    WriterCRUD->>Shrecknet: load_context_and_data_worker
    Shrecknet-->>WriterCRUD: context
    WriterCRUD->>Shrecknet: extract_metadata_and_chunks_worker
    Shrecknet-->>WriterCRUD: metadata & chunks
    WriterCRUD->>LLMWorker: process_chunks_worker
    LLMWorker-->>WriterCRUD: pairs
    WriterCRUD->>LLMWorker: merge_and_deduplicate_worker
    LLMWorker-->>WriterCRUD: suggestions
    WriterCRUD-->>Celery: suggestions
    Celery-->>API: write analysis.json
    API-->>User: job status
```

## Generate Pages Job

```mermaid
sequenceDiagram
    participant User
    participant API as POST /agents/{id}/pages/{page_id}/generate_job
    participant Celery as task_generate_pages_job
    participant WriterCRUD as crud_agent_writer.generate_pages
    participant LLM as ChatOpenAI
    participant DB as crud_page
    User->>API: request page generation
    API->>Celery: enqueue job + request data
    Celery->>WriterCRUD: generate_pages(agent, page, specs)
    WriterCRUD->>LLM: prompts via ChatPromptTemplate
    LLM-->>WriterCRUD: new or updated content
    WriterCRUD->>DB: create/update pages
    DB-->>WriterCRUD: saved pages
    WriterCRUD-->>Celery: result summary
    Celery-->>API: write generated.json
    API-->>User: job status
```

* **task_analyze_pages_job** and **task_generate_pages_job** run in Celery workers and call into `crud_agent_writer`.
* **agentic_worker_shrecknet** and **agentic_worker_llm** provide context extraction and LLM utilities.
* **ChatOpenAI** produces page text which is persisted via `crud_page`.
