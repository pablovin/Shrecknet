# Unreleased: weighted CharacterAgent evidence and cross-scene Steadiness

Stage 3 now returns eight nullable polarity/source/reason slots with compact
bipolar definitions. Canonical evidence receives backend weight 2 and grounded
perspective preferences weight 1; one strongest observation per event and trait
contributes. Normal model call count and roles remain unchanged.

New v4 profiles use weighted recency/smoothing and cross-scene Steadiness, with a
neutral 5/baseline initialization, per-trait repeat caps, and independent-event
support thresholds. Manual overrides preserve inference. Historical v3 snapshots
retain original calculations and comparable-context Steadiness. Optional scene
event grouping and unresolved flags prevent reviewed retellings from inflating
support; paraphrases require reviewed grouping, not automatic text similarity.

Deploy matching API/workers and upgraded strict SDK/frontend readers together.
No SQL schema migration or automatic re-extraction occurs. Preserve pilot exports;
old code can reject new records, so rollback needs compatible readers or saved
data with the matching release. See the canonical
[trait contract](../Documentation/Agents/CharacterAgent/Dispositional%20Traits.md).
