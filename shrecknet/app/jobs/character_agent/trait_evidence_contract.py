"""Stage 3 trait definitions and the complete model-facing observation contract.

After Stage 1 binds perspectives, Stage 3 evaluates canonical character behavior
in parallel with Stage 2 psychology. Each scene returns all eight directional
slots, each null or a polarity/context/justification observation. Context enums
come from the authoritative registry. The backend binds provenance and reduces
observations into directional points and comparable-context STEADINESS; this
prompt never produces a point or public profile.
"""
import json

from app.schemas.character_traits import DIRECTIONAL_TRAITS, TRAIT_BY_KEY


def trait_context_values(trait: str) -> tuple[str, ...]:
    """Enumerate complete comparable contexts; unspecified remains directional evidence."""
    return ("unspecified", *(
        f"{diagnostic}:{relationship}:{stakes}"
        for diagnostic in TRAIT_BY_KEY[trait].diagnostic_situations
        for relationship in ("friend", "enemy", "other")
        for stakes in ("ordinary", "high_stakes")
    ))


# Purpose: Supply definitions, both poles, and construct exclusions from one registry.
# Used by: TRAIT_INTERPRETATION_PROMPT, Stage 3 before observation selection.
# Expected: Character-specific observations that discriminate the correct construct/pole.
TRAIT_DEFINITION_CONTRACT = (
    "\nEstablished personality dimensions and meanings:\n"
    + json.dumps([
        {"name": key, "definition": TRAIT_BY_KEY[key].definition,
         "low_pole": TRAIT_BY_KEY[key].left_pole,
         "high_pole": TRAIT_BY_KEY[key].right_pole,
         "boundary_notes": TRAIT_BY_KEY[key].boundary_notes}
        for key in DIRECTIONAL_TRAITS
    ], ensure_ascii=False)
)

# Purpose: Define the required eight slots, complete observations, and admission rules.
# Used by: TRAIT_INTERPRETATION_PROMPT, Stage 3; composed after TRAIT_DEFINITION_CONTRACT.
# Expected: One ordered eight-slot object per scene, with zero to eight observations.
TRAIT_EVIDENCE_CONTRACT = r"""
OUTPUT: Return JSON only, exactly one result per input scene in the same order:
{"scene_trait_interpretations":[{
 "integrity":null,"caution":null,"presence":null,"forbearance":null,
 "diligence":null,"curiosity":null,"sharing":null,"restlessness":null
}]}.
All eight trait keys are required for EVERY scene. Additional keys are forbidden.
Evaluate each dimension; null means no supported observation for that dimension.
A non-null slot must contain exactly these three required keys:
{"polarity":"low|high", "context":"one allowed context for this trait",
 "justification":"one brief reason naming the character's canonical action or explicit words and why they support this pole"}.
polarity must be low or high. justification must be nonblank and at most 360
characters. Each slot allows one observation; a scene may support zero through
eight traits. There is no three-trait cap and no minimum number of observations.

context is a single enum string, never an object, null, or separate fields.
A specified context has format diagnostic:relationship:stakes. The diagnostic
identifies the trait-specific situation; relationship is friend, enemy, or other
and describes the target of this character's response; stakes is ordinary or
high_stakes and describes the scene-supported consequences. Use unspecified
whenever any comparison detail cannot be grounded. Do not invent relationship,
stakes, or circumstances to select a context. unspecified observations still
support directional traits but cannot contribute to STEADINESS. A null slot
means no observation, not missing context. Context uncertainty alone must not
cause omission of an otherwise supported observation.
Allowed context values by trait:
""" + json.dumps({key: trait_context_values(key) for key in DIRECTIONAL_TRAITS}, ensure_ascii=False) + r"""
The backend maps context to persisted situation_type and supplies all identifiers
and provenance. Never output trait names inside observations, IDs, points,
numeric intensity, confidence, extra context fields, or a STEADINESS observation.

One diagnostic voluntary action or explicit value statement is enough for an
observation; accumulated independent observations establish the trait estimate.
A clearly voluntary character-specific action can be diagnostic without an
explicitly narrated alternative. Do not invent alternatives or motivations, and
do not mistake lack of documented alternatives for lack of voluntary choice.
Omit compelled, ambiguously attributed, merely witnessed, group-only, or
non-diagnostic behavior. Known constraints, immediate necessity, and orders can
make an action non-diagnostic. A temporary feeling or isolated outcome alone is
insufficient; a diagnostic response within one scene can count without proving
an enduring personality tendency. Both poles are equally admissible and are not
moral labels. No balance quota applies.
"""
