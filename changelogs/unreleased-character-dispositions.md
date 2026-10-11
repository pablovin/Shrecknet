# Unreleased: CharacterAgent dispositional personality

Breaking replacement of CharacterAgent personality with evidence-grounded
INTEGRITY, CAUTION, PRESENCE, FORBEARANCE, DILIGENCE, CURIOSITY, SHARING,
RESTLESSNESS and separate STEADINESS. Profiles store z anchors and expose 1–9
points with explicit unknown/uncertain states. Administrator overrides are audited.

Embodiment processes sequential source chunks of up to ten scenes, using four
normal calls per chunk plus authored initialization. Revisions retain diagnostic
evidence and precise provenance, including no-change observations. Query framing
selects affordances and supplies STEADINESS separately from directional traits.
The SDK adds typed profiles, point edits, metadata and raw-evidence inspection.

Drain old jobs, clear old CharacterAgent aggregates and SQL drafts/checkpoints,
deploy matching consumers, and regenerate. No trait conversion or SQL schema
migration is provided. See the [canonical feature and operational contract](../Documentation/Agents/CharacterAgent/Dispositional%20Traits.md).


## Personality-conditioned decision-making follow-on

Decision deliberation now appraises situational constraints and relevant
behavioral tendencies inside the existing single call. Identity mode always
includes compact effective numerical directions and separate evidence-checked
Steadiness. The existing `use_character_identity` request flag is the only
personality switch; generic mode excludes all character personality inputs. Existing public requests/results, owner-memory isolation, and bounded
JSON repair remain compatible. Missing/obsolete profiles preserve portrait-only
behavior. There is no migration or automatic portrait replacement for this
follow-on change. Reasoning remains configurable without a default change.

The evaluation runner isolates portrait, prompt, numerical-context, and reasoning
comparisons, retains failed trials, and records latency and input estimates.
Live comparisons and the reviewed pilot remain pending. See the
[current decision contract and rollout](../Documentation/Agents/CharacterAgent/Query/Query.md).
