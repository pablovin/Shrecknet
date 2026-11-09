# Companion simple research planner design

Status: implemented. The canonical behavior and ordered stages are documented in
[Companion](./Companion.md#execution-and-ownership); public compatibility is in
[Frontend API](./Frontend%20API.md#simple-research-planning-compatibility).

The planning model makes semantic decisions; the backend makes execution decisions.

```json
{"mode":"search","tasks":[{"question":"What are the Telmori playable traits?","sources":["shreckworld","atlas"]},{"question":"What is the RuneQuest character creation sequence?","sources":["shreckworld"]}]}
```

`SimpleCompanionPlan` and `ResearchTask` replace the rich model-facing contract.
`QueryPlan`, `Need`, `PlannedCall` and `Assessment` remain internal execution and
saved-diagnostic contracts. The job-local `planning.py` owns initial compilation,
technical fallback and assessment follow-up compilation. Existing scope service
and repository owners retain persistence, authorization, transactions and leases.

Scope/continuation decisions run before research planning. Memory relevance is
checked only for proposed memory sources against the original user goal. Public
query preparation serves both initial Web tasks and automatic recovery, without
another source-selection vote. Assessment retains detailed support and conflicts
but requests semantic follow-up tasks instead of executable tool parameters.

Three tasks with three sources permit nine initial calls. Recovery remains one
round/two calls by default. Twenty records and 10,000 estimated evidence tokens
remain the default shared evidence budget. Stable round-robin allocation prevents
fast-completing calls from consuming all need coverage. Technical planner failures
retain the original goal and run bounded local searches; they do not ask fake
clarification. Assessment failure continues to use compact retry and safe extracts.

The existing four-routing-call reserve covers scope/continuation, research planning,
optional memory relevance and one public-query batch. Existing 600-second call,
615-second retrieval-round and 10,200-second turn limits and saved-timeout
migrations are unchanged. No new SQL migration, graph rewrite or re-embedding is
required. Drain active turns and deploy prompts, schemas and backend together.

See [validation](./Validation.md) for regression checks and deployment limitations.
