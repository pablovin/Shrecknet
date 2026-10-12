# Unreleased — Novelist V5 Narrative Director

- Derive cast and Director identity restrictions from a single validated identity
  list; prevent contradictory duplicate mappings from failing otherwise valid analysis.
- Restore compact whole-source narrative planning with a validated 12-beat maximum
  for extraction and oversized synthesis. Replan over-limit output once without
  dropping the ending; keep the existing 6,000-token output allowance.
- Use a focused identity edit over the complete generated unit and explicit
  mappings for identity-only failures. Keep broader regeneration for hard failures,
  share one retry allowance, and revalidate every edit.
- Accept persistent player/GM name mentions as editorial warnings after one writer
  retry when hard checks pass; retain the draft and expose per-block warnings and
  quality-gate counts. Empty/truncated, malformed, repeated, or gameplay prose
  remains a hard failure.
- Extract certainty and identity evidence before a compact editorial Director call.
- Write Director-owned arcs with relevant source passages and a 700-character
  accepted-prose transition tail; retain bounded context/output subdivisions.
- Add conservative identity/gameplay/repetition checks and provider-usage metrics.
- Preserve API payloads, HTML output, persisted progress enums, historical plans,
  and existing model/context settings; no database migration is required.
- Add Session 9 annotated regression evidence, recorded baseline metrics, and a
  reproducible context comparison. Live token/latency and prose quality evaluation
  remains pending.

See the [canonical Novelist contract](../Documentation/Agents/Novelist/Novelist.md)
and [evaluation record](../Documentation/Agents/Novelist/Narrative%20Planning%20and%20Source%20References%20Plan.md).
