# Read ShreckWorld evidence from Python

The independent ShreckWorld service uses the caller's current Shrecknet token. Configure `shreckworld_base_url` separately from the upstream Shrecknet URL. Management remains restricted; `sdk.shreckworld` provides typed read-only world/source discovery, evidence questions, and rule review for authenticated users.

```python
from shrecknet_client import Shrecknet, SelectedPassage

async with Shrecknet(token=token, shreckworld_base_url="http://localhost:8121") as sdk:
    worlds = await sdk.shreckworld.worlds()
    sources = await sdk.shreckworld.sources(worlds[0].id)
    answer = await sdk.shreckworld.query(
        worlds[0].id, "How does mounted combat work?",
        library_item_ids=[source.id for source in sources[:2]],
        mode="answer",
    )
    # answer.claims contain structured claims and citation_ids.
    review = await sdk.shreckworld.resolve_action(
        worlds[0].id, "Attack while mounted", facts=["The rider is charging"],
    )
    # Inspect review.status, missing_facts and citations before taking an action.
```

A query can scope to one source with the compatible `library_item_id` or to several with `library_item_ids`; omit both to search all sources. `mode="evidence"` is the default and preserves extractive responses. Use `mode="answer"` for ShreckLLM-generated claims, which carry source `citation_ids`; inspect `answer_status`, `missing_information`, `conflicts`, and `source_coverage` before relying on the result. The server validates cited IDs but does not independently prove semantic entailment. Answer mode reports `partial` and describes coverage gaps when selected sources need indexing/reindexing or retrieved passages do not fit the synthesis evidence budget.

A selected passage can be sent with `selection=SelectedPassage(library_item_id=source.id, source_sha256=source.source_sha256, page_number=12, text="Selected text")`. Physical page numbers are one-based. A changed revision returns `409`; mismatched book scope or invalid fields return `422`. The transport reuses existing SDK error mapping and reads the current identity token on every request, including after login/token changes. The SDK context manager closes both upstream and ShreckWorld transports.

Answers are extractive evidence, not conversational synthesis. `source-priority-v1` reviews report `ambiguous` when applicability/edition is unmodeled and `unsupported` without evidence; they do not emit executable `resolved` actions. No game state is changed and existing agents are not automatically rewired. Original PDFs remain readable when the semantic model is unavailable.

For a complex question, opt into `retrieval_strategy="planned"` to get a
ShreckWorld-owned evidence plan, per-need coverage, and evidence groups. Planned
retrieval supports evidence mode only; `mode="answer"` with this strategy returns
`422`. It does not call Elder or Librarian and it does not synthesize an answer.
Use `include_trace=True` to inspect planning, retrieval, coverage, fallback, and
bounded follow-up diagnostics.

For asynchronous execution, `start_agent_query(world_id, question, ...)` returns
a `KnowledgeAgentJob`; poll it with `agent_job(world_id, job_id)`. Its `status`
and `stage` expose lifecycle progress, and its `result` contains the same typed
`KnowledgeAnswer` returned by the synchronous planned path. Only the submitting
user or an admin can read the job. Jobs cannot currently be cancelled.

Admins and world builders can read or change the service-owned ShreckWorld
provider/model target with `model_settings()` and `update_model_settings(...)`.
The one `model_shreckworld_synthesizer` target is shared by planner, evidence
reviewer, and grounded answer calls:

```python
from shrecknet_client import KnowledgeModelTarget, Shrecknet

async with Shrecknet(token=admin_token) as sdk:
    current = await sdk.shreckworld.model_settings()
    updated = await sdk.shreckworld.update_model_settings(
        KnowledgeModelTarget(provider="openai", name="gpt-5-nano")
    )
```

See the [ShreckWorld model settings API](../../../Documentation/API/SHRECKWORLD_READER_API.md#shreckworld-model-settings)
for authorization, persistence, and environment-default precedence.

See the [reader API contract](../../../Documentation/API/SHRECKWORLD_READER_API.md) for byte ranges, citation anchors, access rules, rollout and failure states. Run `examples/11_shreckworld/read_world.py --help` for a runnable client example.
Run `examples/11_shreckworld/run_agent_query.py --help` for the asynchronous planned-evidence job workflow.

Typed citations also include `provenance`, `rectangles`, `heading_path`,
`page_numbers`, `evidence_text`, and `content_url` for structured semantic
indexes. Use `query(..., include_trace=True)` to diagnose excluded legacy/model
indexes and retrieval fallbacks. See [indexing/readiness](shreckworld-upload.md)
for `readiness()`, `start_indexing()`, and `indexing_job()`; reading remains
available when model readiness fails.
