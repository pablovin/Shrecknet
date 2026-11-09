# Unreleased Companion conversational simplification

- Replace evidence assessment, structured claim drafts, semantic verification and
  memory relevance voting with router-selected parallel tools and cited Markdown.
- Allow any router-generated Web query when the tool is configured and allowed;
  retain Chronicle, identity/traits, subjective memory, private chat and rapport.
- Resolve citations deterministically against the supplied evidence projection;
  retain final authorization/revision checks and honest partial-source diagnostics.
- Retry length-truncated synthesis once with the same evidence; report typed
  generation/source failures instead of generic missing-evidence refusals.
- Compose optional rich blocks from cited source metadata without model calls;
  render safe Markdown in Foundry and preserve text when media/rendering fails.
- Keep existing API/rich envelopes and inactive model/config keys compatible.
  New/missing completion ceilings default to 8,000; saved values remain unchanged.

See [the canonical contract](../Documentation/Agents/Companion/Companion.md) and
[validation/rollout](../Documentation/Agents/Companion/Validation.md). No database
migration or re-embedding is required. Drain active turns and deploy matching API
and workers together; real provider quality/latency remains a deployment gate.
