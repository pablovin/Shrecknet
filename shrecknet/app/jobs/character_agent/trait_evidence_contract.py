"""Stage 3 trait definitions and the complete model-facing observation contract.

After Stage 1 binds perspectives, Stage 3 evaluates canonical and perspective evidence
in parallel with Stage 2 psychology. Each scene returns all eight directional
slots, each null or a polarity/source/reason observation. The backend binds provenance and reduces
observations into directional points and cross-scene consistency; this
prompt never produces a point or public profile.
"""
from app.schemas.character_traits import DIRECTIONAL_TRAITS, TRAIT_BY_KEY


# Purpose: Supply compact bipolar meanings from the authoritative registry.
# Used by: TRAIT_INTERPRETATION_PROMPT, Stage 3 extraction.
# Expected: Independently classified evidence for each construct.
TRAIT_DEFINITION_CONTRACT = "\nTraits (low | high):\n" + "\n".join(
    f"{key}: {TRAIT_BY_KEY[key].compact_poles[0]} | {TRAIT_BY_KEY[key].compact_poles[1]}"
    for key in DIRECTIONAL_TRAITS
) + "\nIntegrity is not Sharing; Curiosity is not Restlessness; Presence is not leadership; Forbearance is not general kindness; Caution is not simply inverse courage.\n"

# Purpose: Define every required nullable slot and the complete candidate shape.
# Used by: TRAIT_INTERPRETATION_PROMPT after TRAIT_DEFINITION_CONTRACT.
# Expected: One ordered eight-slot object per scene; backend binds provenance.
TRAIT_EVIDENCE_CONTRACT = r"""
Return JSON only, one result per input scene in the same order:
{"scene_trait_interpretations":[{
 "integrity":null,"caution":null,"presence":null,"forbearance":null,
 "diligence":null,"curiosity":null,"sharing":null,"restlessness":null
}]}.
All eight keys are required. Each is null or exactly:
{"polarity":"high","source":"canonical","reason":"Brief grounded reason"}.
polarity: low or high. source: canonical or perspective. reason: nonblank,
at most 360 characters, explaining the character-specific signal and direction.
Use null when neither pole has meaningful support. Zero through eight non-null
slots are valid. Additional keys are forbidden. Supply no identifiers, numerical
values, confidence, or context classifications; the backend binds provenance.
"""
