# Librarian Agent

Librarian is a PDF-grounded Query v2 pipeline over structured `PdfChunk`
documents in Neo4j.

## Runtime

The only supported query path is:

1. Plan standalone information needs from the original question.
2. Retrieve each need using vector, contextual full-text, and exact-term search.
3. Fuse and rerank child matches, then expand them into complete graph evidence.
4. Validate evidence coverage and run up to two follow-up retrieval passes.
5. Synthesize once with the agent's style and stable source-ID citations.

The endpoint is `POST /jobs/librarian/{agent_id}/query`. `context` mode skips
only final synthesis; planning, retrieval, and validation still run.

## Code Ownership

- `librarian.py`: stable application entrypoint.
- `query_v2.py`: evidence-loop orchestration.
- `retrieval_strategies.py`: Neo4j retrieval, ranking, and graph expansion.
- `prompts.py`: all Librarian LLM prompts.
- `citations.py`: trusted citation extraction and rendering.
- `schemas.py`: request, response, and evidence contracts.
- `debug_artifacts.py`: numbered stage artifacts and run manifest.

See [Embedding](Embedding/Embedding.md), [Embedding Packages](Embedding/Embedding%20Packages.md), [Query](Querry/Querry.md), and
[Retrieval](Retrieval/Retrieval.md) for detailed contracts.
