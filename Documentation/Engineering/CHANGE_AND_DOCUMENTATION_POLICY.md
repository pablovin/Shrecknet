# Change and Documentation Policy

This page defines the completion contract used by the Shrecknet Maintainer Agent in
the repository root `AGENTS.md`. Documentation is a deliverable, not a follow-up task.

## Change completion matrix

| Change | Required code companions | Required documentation |
| --- | --- | --- |
| New feature | Focused tests; SDK support | Feature page, index, API/SDK docs |
| Business-rule change | Boundary/regression tests | New rule, invariants, side effects |
| API contract change | API and SDK tests | Contract, errors, compatibility, examples |
| Persistence/graph change | Migration/integrity tests | Model, migration, deployment, rollback |
| Job or agent change | Lifecycle/failure tests | States, effects, observability, recovery |
| Configuration change | Validation/default tests | Default, precedence, secrets, restart |
| Internal refactor | Existing tests remain green | Architecture docs if ownership moves |
| Test-only or typo-only change | Relevant checks | None unless the correction changes a contract |

## Where documentation belongs

- `Documentation/Architecture/`: component boundaries and system-wide flows.
- `Documentation/SceneCentricMemory/`: scene, milestone, embedding, and retrieval contracts.
- `Documentation/Agents/<Agent>/`: agent behavior, workflows, prompts, and endpoints.
- `Documentation/API/`: feature-oriented public HTTP contracts when that category exists.
- `Documentation/Database/`: schema, migration, backfill, and database operations.
- `Documentation/Deployment/`: installation, configuration, rollout, and recovery.
- `Documentation/Engineering/`: repository-wide engineering policies.
- `python_sdk/docs/`: Python SDK reference and usage.

Prefer updating the established canonical page. Do not create an implementation
summary when a maintained feature page can express the same information. Avoid
duplicating content: link to a single source of truth and keep each page's scope
explicit.

## Feature-document template

Use only the sections that apply, while keeping the contract complete:

```markdown
# Feature name

## Purpose
What problem this solves and who uses it.

## Contract
Inputs, outputs, authorization, invariants, and failure behavior.

## Flow and ownership
Entry point and the router/task, service/job, repository/integration, and storage owners.

## Side effects and operations
Jobs, events, graph/database writes, configuration, monitoring, retries, and recovery.

## Examples
Minimal, accurate examples of the public behavior.

## Compatibility and migration
Changed behavior, rollout/backfill needs, and compatibility guarantees.

## Related documentation
Links to canonical architecture, endpoint, and SDK pages.
```

## Pull-request or handoff checklist

- [ ] Existing code and documentation were searched before introducing a new abstraction.
- [ ] Business logic has one clear owner and is not duplicated across entry points.
- [ ] Boundary contracts and all affected callers were updated together.
- [ ] Focused success, failure, authorization, and isolation tests were considered.
- [ ] Canonical documentation describes the resulting behavior.
- [ ] `Documentation/README.md` links every new or moved page.
- [ ] SDK docs and examples match public API changes.
- [ ] Relevant checks were run and any omissions are reported.
