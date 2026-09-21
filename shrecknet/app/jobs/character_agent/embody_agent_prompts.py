"""Sequential source-chunk embodiment contracts.

Stage 0 initializes authored dispositions. For each chronological chunk, stage 1
renders scene perspectives; stage 2 reasons about immediate effects; stage 3
extracts diagnostic observations; stage 4 reasons over cumulative structured
trait evidence and proposes profile changes. The backend verifies every stage,
computes STEADINESS, and persists revisions. Reflection is presentation only.
All scenes in a chunk use its starting identity; changes take effect at its end.
"""
import json
from app.schemas.character_traits import trait_metadata

TRAIT_CONTRACT = "\nAuthoritative trait definitions and scale:\n" + json.dumps(trait_metadata(), ensure_ascii=False)
PROMPT_VERSION = "character-embodiment-v12-dispositions"

PERSPECTIVE_PROMPT = r"""You are incorporating a character's identity into canonical objective scenes.

Scenes are immutable objective evidence and are listed earliest to latest. For
each scene, produce one grounded subjective perspective and one expressive
first-person character_reflection. Never rewrite a misunderstanding, suspicion,
or uncertainty as an objective scene fact.

Use only facts available to the character at each scene. The whole chunk is
visible to you, but later revelations must never become earlier knowledge.
Every perspective cites its own or earlier supplied scenes in evidence_ids.
Unknown traits remain unknown, not midpoint. Trait values bias behavior and do
not mandate it. STEADINESS modifies consistency, never sampling temperature.
Return every perspective in the same order as the input scenes, using the exact scene_id for each.

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
    "trait_profile": {"dispositional_traits": {"canonical trait key": {"z": "anchor or null", "point": "1..9 or null", "status": "unknown | provisional | supported | contested | manual"}}, "steadiness": "separate estimate"},
    "aspects": [{"id": "stable id", "name": "...", "category": "...", "description": "..."}],
    "goals": [{"id": "stable id", "title": "...", "description": "...", "goal_type": "..."}]
  },
  "scenes": [
    {"scene_id": "id", "name": "scene name", "description": "scene description", "created_at": "ISO timestamp or null"}
  ],
  "required_output": "<ScenePerspectiveOutput schema for one perspective>"
}

OUTPUT — return an object with exactly one key "perspectives":
{
  "perspectives": [
    {
      "scene_id": "exact input scene_id",
      "evidence_ids": ["scene:own-or-earlier-input-scene-id"],
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

Return JSON only. required_output is authoritative if this description and the schema differ."""


ENRICHMENT_PROMPT = r"""You are enriching grounded scene perspectives with immediate psychological effects.

Canonical scenes remain objective. Interpretations are subjective evidence.
The presentation-only character_reflection is intentionally absent. For every
input scene return exactly one same-order result. Return empty lists when an
effect is not warranted. Do not create or update profile state. Do not use later revelations for earlier beliefs.
Cite only the current or earlier supplied scene in each evidence_ids list.

Impacts may target only stable IDs supplied in current_profile. Goal impacts
allow advanced or threatened. Aspect impacts allow created, reinforced, or
invalidated.

INPUT:
{
  "scenes": [{"scene_id":"id","name":"...","description":"...","created_at":null}],
  "perspectives": [{
    "scene_id":"id","source_type":"participated | witnessed | heard_about | read_about | inferred | unknown",
    "awareness_level":0..100,"confidence":0..100,"summary":"...","interpretation":"...",
    "memory_strength":0..100,"importance":1..5
  }],
  "current_profile": {
    "aspects": [{"id":"stable id","name":"..."}],
    "goals": [{"id":"stable id","title":"..."}]
  }
}

OUTPUT:
{
  "scene_enrichments": [{
    "scene_id":"exact input scene_id",
    "evidence_ids":["scene:own-or-earlier-input-scene-id"],
    "emotions":[{"arousal":0..100,"valence":-100..100,"description":"..."}],
    "beliefs":[{"statement":"...","confidence":0..100,"status":"suspected | believed | confirmed | doubted | disproven | superseded"}],
    "impacts":[{"impact_type":"goal_change | aspect_change","target_id":"supplied stable id","direction":"advanced | threatened | created | reinforced | invalidated","magnitude":0..100,"description":"..."}]
  }]
}

Return JSON only. required_output is authoritative if this description and the schema differ."""


TRAIT_EVIDENCE_CONTRACT = r"""
Every trait_evidence item has exactly these fields:
{
  "trait":"integrity | caution | presence | forbearance | diligence | curiosity | sharing | restlessness",
  "evidence_kind":"behavior | authored_disposition",
  "situation_type":"one diagnostic_situations value from that trait's definition",
  "direction":"low | midpoint | high",
  "expression_point":"integer 1..9 or null if not estimable",
  "diagnosticity":0.0,
  "confidence":0.0,
  "behavior":"concise observed choice or explicitly authored stable disposition",
  "justification":"why this reveals this construct, addressing neighboring-trait boundaries",
  "evidence_ids":["exact nonempty allowed canonical evidence references"],
  "episode_id":"scene:exact-scene-id for behavior; identity:exact-entity-id for authored baseline",
  "available_after_scene_id":"latest scene ID cited, without scene: prefix; null for authored baseline",
  "conditions":{
    "knowledge":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "capability":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "options":{"status":"supported | contradicted | unknown","justification":"grounding"},
    "freedom":{"status":"supported | contradicted | unknown","justification":"grounding"}
  },
  "comparison_context":"stable concise description of comparable stakes, relationship, role and choices; null when comparability is uncertain"
}
confidence and diagnosticity are bounded 0..1 judgments, not psychometric precision.
Direction must agree with the point: 1..4 low, 5 intermediate, 6..9 high.
The expression is one observation, not a final personality verdict. Null is
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
Emit at most one item per trait and canonical episode. Cite its episode. Cross-scene
recurrence cites all supporting scenes and becomes available at the latest one.
Do not manufacture independence by repeating one act. Different trait-relevant
choices can yield different evidence. Preserve contradictions and evidence gaps.
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

OBSERVATIONS_PROMPT = r"""You are distilling character observations from canonical scene bundles.

Given canonical scenes, grounded interpretations, and immediate psychological enrichment, produce grounded
observations. Every observation must cite at least one scene evidence_id from the perspectives.
Expressive character_reflection text is presentation-only and is never supplied as evidence.
For every observation category, return [] when there are no grounded findings.
Never return placeholder items such as "none observed", "unknown", or "not
applicable". Omit every item that cannot cite at least one allowed evidence ID.

EVIDENCE ID FORMAT: use only an exact value from allowed_evidence_ids. Never
invent an ID, copy a scene name as an ID, or cite a scene outside this source bundle.

INPUT:
{
  "identity": {"alias":"...","subtitle":null,"entity_type":"...","entity_type_description":null,"properties":{}},
  "allowed_evidence_ids": ["scene:exact-scene-id"],
  "scene_bundles": [
    {
      "scene": {"scene_id":"id","name":"...","description":"...","created_at":null},
      "perspective": {
        "scene_id":"id","source_type":"...","awareness_level":0..100,
        "confidence":0..100,"summary":"...","interpretation":"...",
        "memory_strength":0..100,"importance":1..5
      },
      "emotions": [...],
      "beliefs": [...],
      "impacts": [...]
    }
  ],
  "required_output": "<EmbodimentObservationsOutput schema>"
}

OUTPUT:
{
  "trait_evidence": ["objects with the complete trait-evidence contract below"],
  "recurring_behaviours": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "motivations": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "values": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "fears": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "conflicts": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "relationships": [{"text": "person -> relationship description", "evidence_ids": ["scene:scene_id"]}],
  "contradictions": [{"text": "contradicting behaviours or information", "evidence_ids": ["scene:scene_id"]}],
  "evidence_gaps": [{"text": "what is unknown", "evidence_ids": ["scene:scene_id"]}],
  "subtitle_change": {
    "operation": "retain | set | clear",
    "subtitle": "new concise subtitle or null",
    "justification": "why the subtitle should change",
    "confidence": 0.0 to 1.0,
    "evidence_ids": ["scene:scene_id"]
  }
}
subtitle_change is OPTIONAL. Only include it when the scenes clearly warrant a new or cleared subtitle.
When omitted or operation is "retain", the character's subtitle stays unchanged.
Omit subtitle_change when a set or clear operation has no allowed evidence ID.

Return JSON only. required_output is authoritative if this description and the schema differ."""

PROFILE_UPDATE_PROMPT = r"""Stage 4 — propose one chronological chunk's cumulative profile update.
This reasoning stage consumes structured evidence; the backend validates changes.
INPUT:
{
 "current_profile":{"trait_profile":"current estimates including unknowns and manual overrides",
  "aspects":[{"name":"...","category":"...","description":null,"importance":1,"intensity":null,"created_at":null}],
  "goals":[{"title":"...","description":"...","goal_type":"...","priority":50,"commitment":50,"created_at":null}]},
 "trait_evidence":["validated cumulative observation records: full trait-evidence fields plus backend id, source_group_id, chronological_position, eligible, exclusions"],
 "observations":{"recurring_behaviours":[],"motivations":[],"values":[],"fears":[],"conflicts":[],"relationships":[],"contradictions":[],"evidence_gaps":[],"subtitle_change":null},
 "allowed_evidence_ids":["canonical scene IDs for aspect/goal updates in this chunk"],
 "limits":{"max_aspects":0,"max_goals":0}
}
OUTPUT — all three arrays, empty when no grounded proposal:
{
 "trait_proposals":[{"trait":"one of the eight directional keys","point":5,
   "observation_ids":["eligible backend trait:... observation IDs for this trait"],
   "justification":"cumulative behavioral basis for anchored estimate",
   "addresses_contradictions":"explicit account of opposing evidence or its absence"}],
 "aspect_updates":[{"operation":"add | update | remove","name":"stable current name for update/remove",
   "category":"identity | role | status | physical | capability | knowledge | preference | attitude | history | null",
   "description":null,"importance":3,"intensity":null,"justification":"...","confidence":0.8,"evidence_ids":["allowed canonical scene reference"]}],
 "goal_updates":[{"operation":"add | update | remove | complete","title":"stable current title for update/remove/complete",
   "description":null,"goal_type":"desire | objective | ambition | obligation | avoidance | survival | null",
   "priority":50,"commitment":50,"basis":"explicit | inferred | null","justification":"...","confidence":0.8,"evidence_ids":["allowed canonical scene reference"]}]
}
Trait points are integers 1..9 with bipolar anchors. Select them only from the
structured trait_evidence, never generic observation adjectives. Cite eligible
observation IDs; one proposal per trait, at most eight. Three independent
behavioral episodes normally establish a centre. Authored evidence is provisional.
Do not average contradictory extremes into a falsely certain midpoint. State
contradictions. RESTLESSNESS requires value choices in multiple source contexts.
Never propose STEADINESS; it is computed separately. Manual effective values remain
overrides while the underlying inferred estimate can develop. Do not propose deltas.
At most two aspect operations and one goal operation. importance is 1..5 or null,
intensity/priority/commitment 0..100 or null, confidence 0..1. Stable name/title,
nonblank justification, confidence, and nonempty allowed evidence_ids are required.
Use complete for achieved goals, remove for obsolete aspects. Backend resolves
maximum ACTIVE capacities by importance/priority then recency. No placeholders.
Trait magnitudes bias behavior probabilistically; they do not mandate choices.
Return JSON only.
""" + TRAIT_EVIDENCE_CONTRACT + TRAIT_CONTRACT

PERSPECTIVE_PROMPT += TRAIT_CONTRACT
OBSERVATIONS_PROMPT += TRAIT_EVIDENCE_CONTRACT + TRAIT_CONTRACT
