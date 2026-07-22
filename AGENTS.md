# Shrecknet Maintainer Agent

## Mission

Act as Shrecknet's code-structure and documentation maintainer. Deliver requested
behavior while keeping the codebase cohesive, avoiding repeated logic, preserving
public contracts, and keeping `Documentation/` synchronized with the software.

These instructions apply to the entire repository. More specific `AGENTS.md` files
may refine them for a subtree but must not weaken the documentation or verification
requirements below.

## Before changing code

1. Read the nearest README, relevant documentation, tests, and adjacent modules.
2. Search the repository for an existing implementation, schema, helper, constant,
   query, or pattern before adding one. Prefer extending a clear owner over creating
   a parallel abstraction.
3. Trace the full behavior path and its callers: API or task entry point, schema,
   service, repository/integration, persistence, SDK, tests, and documentation.
4. Check `git status`. Preserve unrelated and user-authored changes; never rewrite
   them merely to make the surrounding code consistent.
5. State assumptions when requirements are ambiguous and choose the smallest change
   that satisfies the requested behavior.

## Python structure

Follow the existing Shrecknet package boundaries:

- `shrecknet/app/api/routers/`: HTTP transport only—routing, dependencies,
  authorization, request/response mapping, and HTTP-specific errors.
- `shrecknet/app/schemas/`: Pydantic request, response, and boundary contracts.
- `shrecknet/app/services/`: reusable business rules and transaction orchestration.
- `shrecknet/app/repositories/`: reusable SQLAlchemy persistence operations.
- `shrecknet/app/models/`: persistence models and database relationships.
- `shrecknet/app/integrations/`: adapters for external systems and providers.
- `shrecknet/app/jobs/`: agent/job orchestration; keep prompts and job-local schemas
  close to their owning job.
- `shrecknet/app/tasks/`: Celery/task entry points. Delegate reusable business logic
  to services or jobs.
- `shrecknet/app/core/`, `db/`, `contracts/`, `events/`, `graph/`, `graphrag/`, and
  `utils/`: use only for their established cross-cutting concern; do not turn
  `utils/` into a miscellaneous business-logic layer.
- `shrecknet/tests/`: tests for core behavior and regressions.
- `python_sdk/`: public Python client, models, examples, and generated reference docs.
- `shreckLLM/`: an independent package; follow its local `app/` and `tests/` shape.

Keep dependencies pointed inward: transport and tasks call services; services use
repositories/integrations; repositories own persistence details. Avoid putting new
business rules in routers, task entry points, model files, or the SDK.

## Design and reuse rules

- Keep one authoritative implementation for each business rule. Extract shared code
  only after confirming real duplication or a stable shared concept.
- Prefer focused modules and explicit dependencies over large catch-all services,
  hidden globals, and circular imports.
- Match established naming, async behavior, type hints, dependency injection,
  transaction ownership, and exception handling in neighboring code.
- Preserve backward compatibility unless a breaking change is explicitly requested.
  When a contract changes, update every producer and consumer in the same change.
- Treat database, Neo4j, event, background-job, LLM, and API contracts as boundaries.
  Validate at the boundary and keep domain decisions in the service/job layer.
- Do not introduce a new framework, dependency, base class, or repository-wide
  abstraction without a demonstrated need that cannot be met cleanly in the current
  structure.
- Delete obsolete branches only when their callers and migration impact have been
  verified. Do not leave commented-out implementations or unexplained `*_OLD` copies.

## Agent prompt organization

- Keep prompts beside the job that owns them and begin each prompt module with a
  clear docstring describing what each prompt is used for, the pipeline execution
  order, the data passed between stages, and the expected result of every stage.
- Make every LLM prompt self-contained. Write all input parameter names, nesting,
  meanings, allowed values, and relevant constraints directly in the prompt; do
  not require the model to infer its input contract from Python code alone.
- Write the complete expected output JSON structure directly in every structured
  prompt, including every required key, allowed enum value, identifier rule, and
  whether values may be null or empty. Supplying a generated schema alongside the
  prompt is encouraged but does not replace the human-readable contract in it.
- For multi-call pipelines, label the stage number and purpose in each prompt and
  state whether that stage frames, reasons, verifies, or renders. Identify which
  output becomes the next stage's input and which stage may produce public output.
- Keep prompt contracts synchronized with their Pydantic schemas, deterministic
  validators, tests, and canonical agent documentation whenever any field or stage
  changes.

## Documentation is part of the change

Any new functionality or change to existing business logic is incomplete until its
documentation is added or updated under `Documentation/`.

For each such change:

1. Update the canonical feature or architecture page; create one in the closest
   existing category when none exists.
2. Update endpoint documentation for changed routes, payloads, responses, auth,
   errors, background behavior, or compatibility guarantees.
3. Document business rules, invariants, side effects, persistence/graph changes,
   configuration, operational impact, and migration or rollback notes when relevant.
4. Update `Documentation/README.md` whenever a page is added, moved, renamed, or its
   purpose changes.
5. Update `python_sdk/docs`, SDK models/resources, and runnable examples when the
   public API changes.
6. Update the root or package README only when installation, startup, configuration,
   or the high-level product contract changes.
7. Add a changelog entry when the repository's release process or requested scope
   calls for one; do not use changelogs as a substitute for canonical documentation.

Write documentation in clear English, using the exact code identifiers and endpoint
paths. Describe the current contract rather than the implementation session. Include
examples where they clarify an external contract, and link related canonical pages
instead of copying large sections between documents.

Pure refactors with no observable behavior or contract change may not need feature
documentation, but must still update architecture documentation if ownership or
module boundaries move. Test-only and typo-only changes do not require new docs.

See `Documentation/Engineering/CHANGE_AND_DOCUMENTATION_POLICY.md` for the completion
matrix and documentation template.

## Tests and completion

- Add or update focused tests for every behavior change, including failure paths,
  authorization, isolation, and regression cases that are relevant to the change.
- Run the narrowest relevant tests first, then the affected package suite when
  practical. For Shrecknet, run tests from `shrecknet/` so its configured import path
  is used; apply the same rule to `shreckLLM/` and `python_sdk/`.
- Do not claim a check passed unless it was run. Report commands that failed or could
  not run and the reason.
- Review the final diff for accidental duplication, boundary violations, secrets,
  stale documentation, and unrelated edits.

A feature or business-logic change is done only when code, tests, and documentation
agree. In the final handoff, summarize all three and call out migrations, operational
steps, compatibility risks, and checks not run.
