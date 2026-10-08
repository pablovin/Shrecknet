"""Shared compact trait contract for scene-perspective evidence interpretation.

Stage 1 extracts factual behavior; Stage 3 interprets that validated behavior.
Identity descriptions provide context but never trait evidence. The backend binds
perspectives and computes points, while Stage 2 enriches psychological state in
parallel with Stage 3. No prompt produces a point or public trait profile.
"""

# Purpose: Define the small categorical evidence output and omission rules.
# Used by: TRAIT_INTERPRETATION_PROMPT.
# Expected: Trait, polarity, contextual situation, and grounded justification.
TRAIT_EVIDENCE_CONTRACT = r"""
For each supported trait, return exactly:
{"trait":"integrity|caution|presence|forbearance|diligence|curiosity|sharing|restlessness",
 "polarity":"low|high",
 "situation_type":"diagnostic:relationship:stakes or unspecified",
 "justification":"one brief reason grounded in the supplied choice"}.
The diagnostic part must be a diagnostic_situations value for that trait.
Relationship is friend, enemy, or other; stakes is ordinary or high_stakes.
Use unspecified if the context is insufficient for comparison. The backend
attaches perspective and scene references; never output IDs, scores, numeric
intensity, confidence, choice-condition objects, or STEADINESS.
Emit no observation for compelled, ambiguous, merely witnessed, group-only, or
non-diagnostic behavior, or when the character lacks a meaningful alternative.
A contextual value must distinguish forgiveness toward friends from retaliation
toward enemies; unspecified evidence still supports the directional trait.
"""
