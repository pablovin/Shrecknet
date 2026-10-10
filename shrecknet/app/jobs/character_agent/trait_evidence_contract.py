"""Shared compact trait contract for canonical scene-based trait interpretation.

Stage 3 interprets canonical scene facts through validated character perspectives,
with identity summaries as context but no prior personality traits. The backend
binds observations to validated scene perspectives and computes points, while
Stage 2 enriches psychological state in
parallel. No prompt produces a point or public trait profile.
"""

# Purpose: Define the small categorical evidence output and omission rules.
# Used by: TRAIT_INTERPRETATION_PROMPT.
# Expected: Trait, polarity, contextual situation, and grounded justification.
TRAIT_EVIDENCE_CONTRACT = r"""
For each supported trait, return exactly:
{"trait":"integrity|caution|presence|forbearance|diligence|curiosity|sharing|restlessness",
 "diagnostic_situation":"a diagnostic value allowed for the selected trait, or null",
 "relationship":"friend|enemy|other, or null",
 "stakes":"ordinary|high_stakes, or null",
 "polarity":"low|high",
 "justification":"one brief reason naming canonical behavior and any relevant character interpretation"}.
The diagnostic_situation must be in the allowed list for the selected trait.
For a specified context, all three context fields must be non-null. Use null for
all three when context is insufficient for comparison. The backend constructs
the persisted situation_type and attaches perspective and scene references;
never output IDs, scores, numeric
intensity, confidence, choice-condition objects, or STEADINESS.
Emit no observation for compelled, ambiguous, merely witnessed, group-only, or
non-diagnostic behavior, or when the character lacks a meaningful alternative.
A supported voluntary choice, explicitly expressed value, or clearly
demonstrated dispositional response may support a trait when the canonical scene
makes it diagnostic. A strong temporary feeling alone does not
establish a disposition. A contextual value must distinguish forgiveness toward
friends from retaliation toward enemies; unspecified evidence still supports
the directional trait.
"""
