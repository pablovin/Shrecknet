# Read ShreckWorld evidence from Python

The independent ShreckWorld service uses the caller's current Shrecknet token. Configure `shreckworld_base_url` separately from the upstream Shrecknet URL. Management remains restricted; `sdk.shreckworld` provides typed read-only world/source discovery, evidence questions, and rule review for authenticated users.

```python
from shrecknet_client import Shrecknet, SelectedPassage

async with Shrecknet(token=token, shreckworld_base_url="http://localhost:8121") as sdk:
    worlds = await sdk.shreckworld.worlds()
    sources = await sdk.shreckworld.sources(worlds[0].id)
    answer = await sdk.shreckworld.query(
        worlds[0].id, "How does mounted combat work?",
        library_item_id=sources[0].id,
    )
    review = await sdk.shreckworld.resolve_action(
        worlds[0].id, "Attack while mounted", facts=["The rider is charging"],
    )
    # Inspect review.status, missing_facts and citations before taking an action.
```

A selected passage can be sent with `selection=SelectedPassage(library_item_id=source.id, source_sha256=source.source_sha256, page_number=12, text="Selected text")`. Physical page numbers are one-based. A changed revision returns `409`; mismatched book scope or invalid fields return `422`. The transport reuses existing SDK error mapping and reads the current identity token on every request, including after login/token changes. The SDK context manager closes both upstream and ShreckWorld transports.

Answers are extractive evidence, not conversational synthesis. `source-priority-v1` reviews report `ambiguous` when applicability/edition is unmodeled and `unsupported` without evidence; they do not emit executable `resolved` actions. No game state is changed and existing agents are not automatically rewired. Original PDFs remain readable when the semantic model is unavailable.

See the [reader API contract](../../../Documentation/API/SHRECKWORLD_READER_API.md) for byte ranges, citation anchors, access rules, rollout and failure states. Run `examples/11_shreckworld/read_world.py --help` for a runnable client example.

Typed citations also include `provenance`, `rectangles`, `heading_path`,
`page_numbers`, `evidence_text`, and `content_url` for structured semantic
indexes. Use `query(..., include_trace=True)` to diagnose excluded legacy/model
indexes and retrieval fallbacks. See [indexing/readiness](shreckworld-upload.md)
for `readiness()`, `start_indexing()`, and `indexing_job()`; reading remains
available when model readiness fails.
