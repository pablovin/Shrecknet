"""Shared compact trait contract for canonical scene-based trait interpretation.

Stage 3 interprets canonical scene facts without identity descriptions or
generated perspectives. The backend binds observations to validated scene
perspectives and computes points, while Stage 2 enriches psychological state in
parallel. No prompt produces a point or public trait profile.
"""

# Purpose: Define the small categorical evidence output and omission rules.
# Used by: TRAIT_INTERPRETATION_PROMPT.
# Expected: Trait, polarity, contextual situation, and grounded justification.
TRAIT_EVIDENCE_CONTRACT = r"""
For each supported trait, return exactly:
{"trait":"integrity|caution|presence|forbearance|diligence|curiosity|sharing|restlessness",
 "polarity":"low|high",
 "situation_type":"diagnostic:relationship:stakes or unspecified",
 "justification":"one brief reason grounded in the canonical scene and character interpretation"}.
The diagnostic part must be a diagnostic_situations value for that trait.
Relationship is friend, enemy, or other; stakes is ordinary or high_stakes.
Use unspecified if the context is insufficient for comparison. The backend
attaches perspective and scene references; never output IDs, scores, numeric
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
