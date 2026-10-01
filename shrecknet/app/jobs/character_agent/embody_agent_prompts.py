"""Scene-centric source-bundle embodiment prompt contracts.

Stage 0 extracts an authored baseline. For every bounded source chunk, stage 1
turns only its raw scenes into position-bound interpretations. Stage 2 derives
emotions, beliefs, impacts, trait candidates, and durable aspect/goal signals
from those interpretations alone. The backend binds references, validates
outputs, then performs source reduction, STEADINESS computation, and revision
persistence deterministically. No prompt receives a cumulative raw-evidence
ledger.
"""
import json
from app.schemas.character_traits import trait_metadata

TRAIT_CONTRACT = "\nAuthoritative trait definitions and scale:\n" + json.dumps(trait_metadata(), ensure_ascii=False)
PROMPT_VERSION = "character-embodiment-v23-bounded-psychology"

PERSPECTIVE_PROMPT = r"""You are incorporating a character's identity into canonical objective scenes.

Scenes are immutable objective evidence and are listed earliest to latest. For
each scene, produce exactly one compact grounded subjective perspective. Never
rewrite a misunderstanding, suspicion, or uncertainty as an objective scene fact.

Use only facts available to the character at each scene. The whole source bundle is
visible to you, but later revelations must never become earlier knowledge.
The backend assigns every canonical scene and evidence_ids reference. Do not return IDs or
evidence references; return one result for each numbered input position.
No inferred personality profile is supplied to this stage: interpret scenes from
canonical identity and scene facts alone. STEADINESS modifies consistency, never
sampling temperature.
Return every perspective in the same order as the input positions. The backend binds
position 1 to the first scene, position 2 to the second, and so on.

This is compact structured character-state extraction, not prose generation,
narration, roleplay, a memoir, dialogue, or stream of consciousness. Do not
repeat information across fields or retell the scene.

For every perspective:
- summary: exactly one sentence, at most 40 words; state what the character
  understands happened without interpreting motives.
- interpretation: at most two short sentences and 80 words; state what the event
  means to this character without retelling the scene.
- character_reflection: first person, at most two short sentences and 60 words;
  state an immediate personal reaction without dialogue, narration, or monologue.

INPUT:
{
  "identity": {
    "alias": "name or alias",
    "subtitle": "subtitle or null",
    "entity_type": "ontology type",
    "entity_type_description": "type description or null",
    "properties": {"property": "value"}
  },
  "scenes": [
    {"position": 1, "name": "scene name", "description": "scene description", "created_at": "ISO timestamp or null"}
  ],
  "required_output": "one position-bound perspective object as defined below"
}

OUTPUT — return an object with exactly one key "perspectives":
{
  "perspectives": [
    {
      "source_type": "participated | witnessed | heard_about | read_about | inferred | unknown",
      "awareness_level": 0..100,
      "confidence": 0..100,
      "summary": "concise factual summary",
      "interpretation": "grounded subjective interpretation",
      "character_reflection": "expressive first-person reflection in character voice",
      "memory_strength": 0..100,
      "importance": 1..5
    }
  ]
}

Return JSON only."""


PERSPECTIVE_TRUNCATION_RECOVERY_PROMPT = PERSPECTIVE_PROMPT + r"""

RECOVERY INSTRUCTION: Your previous response exceeded the output budget. Produce
the same required perspectives much more compactly. The exact one-perspective-per-
scene count and every field limit are mandatory. Return JSON only."""


ENRICHMENT_PROMPT = r"""Stage 2 — enrich grounded character perspectives. Use only each supplied
perspective; never reconstruct the objective scene, use reflection text, or let
later positions affect earlier ones. Return exactly one enrichment in input order.

Every enrichment MUST contain all six arrays: emotions, beliefs, impacts,
trait_candidates, aspect_signals, goal_signals. Use [] when unsupported. Never
return scene_id, evidence_ids, episode_id, available_after_scene_id, or target_id:
the backend supplies those.

Limits per scene: emotions 2, beliefs 2, impacts 2, trait_candidates 3, aspect
signals 1, goal_signals 1. Descriptions/condition justifications <=240 chars;
beliefs, impacts, and behavior <=300; other justifications <=360.

OUTPUT: {"scene_enrichments":[{"emotions":[{"arousal":0..100,"valence":-100..100,"description":"..."}],"beliefs":[{"statement":"...","confidence":0..100,"status":"suspected|believed|confirmed|doubted|disproven|superseded"}],"impacts":[{"impact_type":"goal_change|aspect_change","target_index":1,"direction":"allowed direction","magnitude":0..100,"description":"..."}],"trait_candidates":[{"trait":"one of eight keys","evidence_kind":"behavior","situation_type":"...","pole":"left|right","update_intensity":"small|medium|large","expression_z":0.1,"diagnosticity":0.0,"confidence":0.0,"behavior":"...","justification":"...","conditions":{"knowledge":{"status":"supported|contradicted|unknown","justification":"..."},"capability":{"status":"supported|contradicted|unknown","justification":"..."},"options":{"status":"supported|contradicted|unknown","justification":"..."},"freedom":{"status":"supported|contradicted|unknown","justification":"..."}},"comparison_context":"... or null"}],"aspect_signals":[{"name":"...","category":"identity|role|status|physical|capability|knowledge|preference|attitude|history","description":"...","importance":1..5,"justification":"...","confidence":0.0}],"goal_signals":[{"title":"...","description":"...","goal_type":"desire|objective|ambition|obligation|avoidance|survival","priority":0..100,"commitment":0..100,"basis":"explicit|inferred","justification":"...","confidence":0.0}]}]}.

Impacts use target_index from the matching current_profile list. goal_change is
advanced|threatened; aspect_change is created|reinforced|invalidated. With no
targets, impacts must be []. Current-profile aspects and goals are impact targets
only: never repeat their name or title in aspect_signals or goal_signals.

Trait candidates are only distinct diagnostic individual choices, never emotion,
group action, or repeated evidence. expression_z is nonzero (-1.9..1.9) and its
sign selects pole (negative=left, positive=right). Pole must agree with expression_z.
Valid situations:
integrity=exploitation|self_serving_deception; caution=uncertain_threat|uncertain_dependence|reliance_without_guarantees;
presence=social_visibility|social_approach|voluntary_contact;
forbearance=provocation|betrayal|obstruction|retaliation;
diligence=unattended_duty|delayed_payoff|cutting_corners|persistence;
curiosity=novelty|exploration|puzzle|unknown_information; sharing=resource_allocation|spoils|rewards;
restlessness=value_conflict|recurring_value_preference.

An aspect signal needs a grounded durable character fact; a goal signal needs a
grounded ongoing personal commitment. They are evidence, not mutations. A
confirmed, character-specific revelation about origin, nature, body, identity,
or constructed status is a mandatory distinct `identity` aspect signal when it
is important to the character. Name the revelation itself (for example,
"<creator>-crafted vessel"), not an existing relationship, role, or goal. Return
JSON only."""


TRAIT_EVIDENCE_CONTRACT = r"""
Every trait_evidence item has exactly these fields:
{
  "trait":"integrity | caution | presence | forbearance | diligence | curiosity | sharing | restlessness",
  "evidence_kind":"behavior | authored_disposition",
  "situation_type":"one diagnostic_situations value from that trait's definition",
  "pole":"left | right",
  "update_intensity":"small | medium | large",
  "expression_z":"non-zero number from -1.9 to 1.9",
  "diagnosticity":0.0,
  "confidence":0.0,
  "behavior":"concise observed choice or explicitly authored stable disposition",
  "justification":"why this reveals this construct, addressing neighboring-trait boundaries",
  "evidence_ids":"assigned by the backend; omit from model output",
  "episode_id":"assigned by the backend; omit from model output",
  "available_after_scene_id":"assigned by the backend; omit from model output",
  "conditions":{
    "knowledge":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "capability":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "options":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "freedom":{"status":"supported | contradicted | unknown","justification":"grounding"}
  },
  "comparison_context":"stable concise description of comparable stakes, relationship, role and choices; null when comparability is uncertain"
}
confidence and diagnosticity are bounded 0..1 judgments, not psychometric precision.
Pole must agree with expression_z: negative left, positive right. Do not emit neutral or non-diagnostic behavior as trait evidence.
update_intensity is required for behavioral evidence: small, medium, or large.
It means this choice's bounded contribution, not its final trait value. The
backend maps those labels to 0.05, 0.10, and 0.20, averages candidates for the
same trait within one source, and applies one source-level update.
The expression_z is one observation, not a final personality verdict. Conditions ask whether the
character knew relevant facts, could perform either action, had both options,
and was free of external compulsion. Unsupported conditions prevent updates.
Prefer no inference to inventing choice. A failed lock is not integrity;
forbidden speech is not low presence; careful failed work is not low diligence;
wrong conclusions do not negate curiosity; known fatal exploration confounds
avoidance. Allocation is sharing, not automatically integrity. Retaliation is
forbearance. Social visibility is not dominance. Exploration is not automatically
low caution or restlessness. RESTLESSNESS requires explicit value choices or
recurring motivated preferences, not merely novelty exposure.
Emit at most one item per trait and canonical episode. Cite its episode. Do not
manufacture independence by repeating one act. Different trait-relevant choices
can yield different evidence. Preserve contradictions and evidence gaps.
STEADINESS is never an extracted trait or direct LLM update. The backend computes
it from repeated comparable behavioral evidence; never equate it with goodness.
"""

BASELINE_PROMPT = r"""Stage 0 — extract authored stable-disposition evidence, not public output.
INPUT: identity {alias, authored_text, properties, entity_type}; allowed_evidence_ids
contains the exact identity:entity-id reference. No generated biography is supplied.
OUTPUT: {"trait_evidence": [items defined below]}. Return [] if unsupported.
Only explicit stable dispositions with concrete diagnostic meaning qualify.
Bare adjectives such as shy or honest are insufficient. evidence_kind must be
authored_disposition. Do not invent behavioral episodes or scene dates. Unknown
choice conditions are allowed for an authored assertion, but it is not behavior.
""" + TRAIT_EVIDENCE_CONTRACT + TRAIT_CONTRACT

PROFILE_UPDATE_PROMPT = r"""Stage 3 — propose one source bundle's cumulative profile update.
This reasoning stage consumes structured evidence; the backend validates changes.
INPUT:
{
 "current_profile":{"trait_profile":"current estimates including unknowns and manual overrides",
  "aspects":[{"name":"...","category":"...","description":null,"importance":1,"intensity":null,"created_at":null}],
  "goals":[{"title":"...","description":"...","goal_type":"...","priority":50,"commitment":50,"created_at":null}]},
 "trait_evidence":[{"position":1,"validated observation fields":"without backend identifiers"}],
 "observations":{"recurring_behaviours":[],"motivations":[],"values":[],"fears":[],"conflicts":[],"relationships":[],"contradictions":[],"evidence_gaps":[],"subtitle_change":null},
 "allowed_evidence_positions":["one-based positions for source-scene evidence"],
 "scene_signals":{"aspects":["durable scene-local aspect evidence"],"goals":["adopted durable goal evidence"]},
 "limits":{"max_aspects":0,"max_goals":0}
}
OUTPUT — all three arrays, empty when no grounded proposal:
{
 "trait_proposals":[{"trait":"one of the eight directional keys",
   "observation_indexes":[1],
   "justification":"cumulative behavioral basis for the evidence-driven update",
   "addresses_contradictions":"explicit account of opposing evidence or its absence"}],
 "aspect_updates":[{"operation":"add | update | remove","name":"stable current name for update/remove",
   "category":"identity | role | status | physical | capability | knowledge | preference | attitude | history | null",
   "description":null,"importance":3,"intensity":null,"justification":"...","confidence":0.8,"evidence_indexes":[1]}],
 "goal_updates":[{"operation":"add | update | remove | complete","title":"stable current title for update/remove/complete",
   "description":null,"goal_type":"desire | objective | ambition | obligation | avoidance | survival | null",
   "priority":50,"commitment":50,"basis":"explicit | inferred | null","justification":"...","confidence":0.8,"evidence_indexes":[1]}]
}
Trait numeric updates are deterministic: do not output a numeric estimate or delta. Use one-based observation_indexes; one proposal per trait, at most eight. One eligible
behavioral observation can establish a bounded centre. Authored evidence is provisional.
Do not average contradictory extremes into a falsely certain centre. State
contradictions. RESTLESSNESS requires an explicit value choice or recurring motivated preference.
Never propose STEADINESS; it is computed separately. Manual effective values remain
overrides while the underlying inferred estimate can develop. Do not propose deltas.
At most two aspect operations and one goal operation. importance is 1..5 or null,
intensity/priority/commitment 0..100 or null, confidence 0..1. Stable name/title,
nonblank justification, confidence, and nonempty evidence_indexes are required.
Use complete for achieved goals, remove for obsolete aspects. Backend resolves
maximum ACTIVE capacities by importance/priority then recency. No placeholders.
Trait magnitudes bias behavior probabilistically; they do not mandate choices.
Create an aspect or goal only when scene_signals establishes a durable change or
commitment. Do not create one merely because a scene is dramatic. Additions must
be traceable to a matching signal; returning no additions is
normal.
Return JSON only.
""" + TRAIT_EVIDENCE_CONTRACT + TRAIT_CONTRACT

PSYCHOLOGICAL_ANALYSIS_PROMPT = ENRICHMENT_PROMPT + r"""
This is the complete psychological-analysis stage. Return emotions, beliefs,
impacts, trait_candidates, aspect_signals, and goal_signals for every
perspective. Each item must cite exactly the current scene conceptually; the
backend assigns evidence_ids by position, so omit them from model output.
"""

# Backend-owned references remain part of the public contract, but are never model supplied.
PERSPECTIVE_PROMPT += "\nThe backend binds every perspective to exactly its own supplied scene and assigns evidence_ids by position; omit them from model output."
ENRICHMENT_PROMPT += "\nEach item must cite exactly the current scene conceptually; the backend assigns evidence_ids by position, so omit them from model output."
