"""Scene-centric source-bundle embodiment prompt contracts.

Stage 0 extracts an authored baseline. For every bounded source chunk, stage 1
turns only its raw scenes into position-bound interpretations. Stages 2 and 3
run in parallel from those interpretations alone: trait extraction emits
emotions, beliefs, impacts, and trait candidates; identity-signal extraction
emits aspect and goal signals. The backend binds references, validates outputs,
then performs source reduction, STEADINESS computation, and revision persistence
deterministically. No prompt receives a cumulative raw-evidence ledger.
"""
import json
from app.schemas.character_traits import trait_metadata

TRAIT_CONTRACT = "\nAuthoritative trait definitions and scale:\n" + json.dumps(trait_metadata(), ensure_ascii=False)
PROMPT_VERSION = "character-embodiment-v20-parallel-bounded-reduction"

PERSPECTIVE_PROMPT = r"""You are incorporating a character's identity into canonical objective scenes.

Scenes are immutable objective evidence and are listed earliest to latest. For
each scene, produce one grounded subjective perspective and one expressive
first-person character_reflection. Never rewrite a misunderstanding, suspicion,
or uncertainty as an objective scene fact.

Use only facts available to the character at each scene. The whole source bundle is
visible to you, but later revelations must never become earlier knowledge.
The backend assigns every canonical scene and evidence_ids reference. Do not return IDs or
evidence references; return one result for each numbered input position.
Unknown traits remain unknown, not midpoint. Trait values bias behavior and do
not mandate it. STEADINESS modifies consistency, never sampling temperature.
Return every perspective in the same order as the input positions. The backend binds
position 1 to the first scene, position 2 to the second, and so on.

INPUT:
{
  "identity": {
    "alias": "name or alias",
    "subtitle": "subtitle or null",
    "entity_type": "ontology type",
    "entity_type_description": "type description or null",
    "properties": {"property": "value"}
  },
  "current_profile": {
    "trait_profile": {"dispositional_traits": {"canonical trait key": {"z": "bounded inferred value or null", "status": "unknown | provisional | supported | contested | manual"}}, "steadiness": "separate estimate"},
    "aspects": [{"name": "...", "category": "...", "description": "..."}],
    "goals": [{"title": "...", "description": "...", "goal_type": "..."}]
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


ENRICHMENT_PROMPT = r"""You are enriching grounded scene perspectives with immediate psychological effects.

You receive only this character's already-grounded scene perspectives, not raw
objective scenes. Derive every result from the supplied numbered perspective's summary,
interpretation, awareness, and confidence. The presentation-only
character_reflection is intentionally absent. Do not reconstruct or supplement
the objective scene, and do not create or update profile state. Do not use later perspectives for earlier beliefs. The backend attaches the
current scene evidence to every generated result and nested candidate.

Every scene result MUST contain all six arrays in the output object. Use [] when
an array has no grounded item; never omit an array.

For an impact, use target_index: the one-based position of its target in the
matching current_profile array. The backend resolves that index to its stable ID.
When both current_profile arrays are empty, impacts MUST be []. Goal impacts
allow advanced or threatened. Aspect impacts allow created, reinforced, or
invalidated.

INPUT:
{
  "perspectives": [{
    "position":1,"source_type":"participated | witnessed | heard_about | read_about | inferred | unknown",
    "awareness_level":0..100,"confidence":0..100,"summary":"...","interpretation":"...",
    "memory_strength":0..100,"importance":1..5
  }],
  "current_profile": {
    "aspects": [{"position":1,"name":"..."}],
    "goals": [{"position":1,"title":"..."}]
  }
}

OUTPUT:
{
  "scene_enrichments": [{
    "emotions":[{"arousal":0..100,"valence":-100..100,"description":"..."}],
    "beliefs":[{"statement":"...","confidence":0..100,"status":"suspected | believed | confirmed | doubted | disproven | superseded"}],
    "impacts":[{"impact_type":"goal_change | aspect_change","target_index":1,"direction":"advanced | threatened | created | reinforced | invalidated","magnitude":0..100,"description":"..."}],
    "trait_candidates":[{"trait":"integrity | caution | presence | forbearance | diligence | curiosity | sharing | restlessness","evidence_kind":"behavior","situation_type":"diagnostic value for trait","direction":"low | midpoint | high","update_intensity":"small | medium | large","expression_z":"number from -1.9 to 1.9 or null","diagnosticity":0.0,"confidence":0.0,"behavior":"one observed individual choice","justification":"why diagnostic","conditions":{"knowledge":{"status":"supported | contradicted | unknown","justification":"..."},"capability":{"status":"supported | contradicted | unknown","justification":"..."},"options":{"status":"supported | contradicted | unknown","justification":"..."},"freedom":{"status":"supported | contradicted | unknown","justification":"..."}},"comparison_context":"string or null"}],
    "aspect_signals":[{"name":"...","category":"identity | role | status | physical | capability | knowledge | preference | attitude | history","description":"durable character-development fact","importance":1..5,"justification":"...","confidence":0.0}],
    "goal_signals":[{"title":"...","description":"adopted durable commitment","goal_type":"desire | objective | ambition | obligation | avoidance | survival","priority":0..100,"commitment":0..100,"basis":"explicit | inferred","justification":"...","confidence":0.0}]
  }]
}

Return JSON only."""

ENRICHMENT_PROMPT += r"""

Direction must agree with expression_z: use low only for a negative value, midpoint only for exactly 0, and high only for a positive value.
Trait candidates cover every distinct diagnostic individual choice expressed in this character's perspective; there is no hard per-scene limit. A perspective's emotion or belief can explain a candidate but is not behavioral evidence by itself. If the perspective reports only a group action without this character's own decision, return no trait candidate. Unknown conditions remain auditable but cannot update a trait. Valid trait/situation pairs: integrity=exploitation|self_serving_deception; caution=uncertain_threat|uncertain_dependence|reliance_without_guarantees; presence=social_visibility|social_approach|voluntary_contact; forbearance=provocation|betrayal|obstruction|retaliation; diligence=unattended_duty|delayed_payoff|cutting_corners|persistence; curiosity=novelty|exploration|puzzle|unknown_information; sharing=resource_allocation|spoils|rewards; restlessness=value_conflict|recurring_value_preference. Direction agrees with expression_z: negative low, 0 midpoint, positive high. update_intensity measures the source-level influence of this one choice: small is subtle, medium is clear, large is unusually decisive; it is not a personality score. The backend applies high as positive and low as negative, averages same-trait evidence within the source, and makes at most one bounded update per trait/source. Aspect and goal signals are evidence, never mutations: emit at most one of each per scene, only for a durable identity/role/capability/value/status change or personally adopted durable commitment expressed in the perspective; never for a passing emotion, generic scene event, group action, assigned task, or reflection. Return JSON only."""


TRAIT_EVIDENCE_CONTRACT = r"""
Every trait_evidence item has exactly these fields:
{
  "trait":"integrity | caution | presence | forbearance | diligence | curiosity | sharing | restlessness",
  "evidence_kind":"behavior | authored_disposition",
  "situation_type":"one diagnostic_situations value from that trait's definition",
  "direction":"low | midpoint | high",
  "update_intensity":"small | medium | large",
  "expression_z":"number from -1.9 to 1.9 or null if not estimable",
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
Direction must agree with expression_z: negative low, 0 intermediate, positive high.
update_intensity is required for behavioral evidence: small, medium, or large.
It means this choice's bounded contribution, not its final trait value. The
backend maps those labels to 0.05, 0.10, and 0.20, averages candidates for the
same trait within one source, and applies one source-level update.
The expression_z is one observation, not a final personality verdict. Null is
unknown; 5 requires actual intermediate behavior. Conditions ask whether the
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
Do not average contradictory extremes into a falsely certain midpoint. State
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

# Legacy recovery prompt. The normal three-stage pipeline does not call this
# prompt: enrichment emits the scene-local candidates directly. Its omitted
# observation arrays remain optional schema fields for backwards-compatible reads.
OBSERVATIONS_PROMPT = r"""Legacy recovery: extract only evidence that can support a later character-trait update.

Use the supplied objective scenes, grounded perspectives, emotions, and beliefs.
Do not use character_reflection: it is presentation-only. Prefer no evidence to
an invented choice. Every evidence_id must exactly match allowed_evidence_ids.

Return JSON only: {"trait_evidence": []}. An optional subtitle_change may be
included only for a clearly supported set or clear operation, with justification,
confidence (0..1), and nonempty evidence_ids. Do not emit recurring_behaviours,
motivations, values, fears, conflicts, relationships, contradictions, or
evidence_gaps; they default to empty lists and are not used by this stage.

Each trait_evidence item must contain: trait
(integrity|caution|presence|forbearance|diligence|curiosity|sharing|restlessness),
evidence_kind=behavior, situation_type, direction (low|midpoint|high),
expression_z (-1.9..1.9 or null), diagnosticity (0..1), confidence (0..1),
behavior, justification, evidence_ids, episode_id (scene:<scene-id>),
available_after_scene_id (bare latest scene id), conditions, and
comparison_context (string or null).

conditions has knowledge, capability, options, and freedom; each is
{"status":"supported|contradicted|unknown","justification":"..."}.
Allowed situation_type values: integrity=exploitation|self_serving_deception;
caution=uncertain_threat|uncertain_dependence|reliance_without_guarantees;
presence=social_visibility|social_approach|voluntary_contact;
forbearance=provocation|betrayal|obstruction|retaliation;
diligence=unattended_duty|delayed_payoff|cutting_corners|persistence;
curiosity=novelty|exploration|puzzle|unknown_information;
sharing=resource_allocation|spoils|rewards;
restlessness=value_conflict|recurring_value_preference.

Point and direction agree: 1..4 low, 5 midpoint, 6..9 high. Record one
diagnostic choice, not a final personality verdict. Do not infer integrity from
generosity, presence from leadership, caution from known fatal danger,
restlessness from one exploration, or steadiness at all. One item per trait and
canonical episode.
Return {"trait_evidence":[]} when no diagnostic choice is supported."""


# Parallel embodiment prompt branches.
TRAIT_ENRICHMENT_PROMPT = ENRICHMENT_PROMPT + r"""
This is the trait-extraction branch. Return emotions, beliefs, impacts, and
trait_candidates only. You MUST return aspect_signals: [] and goal_signals: []
for every perspective. Do not infer durable identity here. Each item must cite exactly the current scene conceptually; the backend assigns evidence_ids by
position, so omit them from model output.
"""

IDENTITY_SIGNALS_PROMPT = r"""Stage 3 — detect durable aspects and goals from grounded character interpretations.

This is independent of trait extraction. You receive only numbered subjective
perspectives, never canonical scenes or character reflections. Return durable
identity signals only when an interpretation establishes a lasting role, state,
capability, value, history, or personally adopted commitment. Passing emotion,
group action, assigned work, and generic dramatic events are not durable signals.
The backend attaches the scene ID and evidence ID by output position. Return one
object for every input position, in the same order. Empty arrays are normal.

INPUT: {"perspectives":[{"position":1,"source_type":"...","awareness_level":0,"confidence":0,"summary":"...","interpretation":"...","memory_strength":0,"importance":1}]}
OUTPUT: {"scene_identity_signals":[{"aspect_signals":[{"name":"...","category":"identity | role | status | physical | capability | knowledge | preference | attitude | history","description":"...","importance":1,"justification":"...","confidence":0.0}],"goal_signals":[{"title":"...","description":"...","goal_type":"desire | objective | ambition | obligation | avoidance | survival","priority":0,"commitment":0,"basis":"explicit | inferred","justification":"...","confidence":0.0}]}]}
Return JSON only."""

# Backend-owned references remain part of the public contract, but are never model supplied.
PERSPECTIVE_PROMPT += "\nThe backend binds every perspective to exactly its own supplied scene and assigns evidence_ids by position; omit them from model output."
ENRICHMENT_PROMPT += "\nEach item must cite exactly the current scene conceptually; the backend assigns evidence_ids by position, so omit them from model output."
