"""Four-call split-phase CharacterAgent embodiment prompts.

Pipeline per source group:
  Step 1 incorporation -> Step 2 enrichment -> Step 3 observations
                                             -> Step 4 profile update

Steps 1-3 analyze an immutable starting-profile snapshot and may run concurrently
across source groups. Step 4 runs in chronological source order against the
latest cumulative profile.
"""

PROMPT_VERSION = "character-embodiment-v8-parallel-analysis"

PERSPECTIVE_PROMPT = r"""You are incorporating a character's identity into canonical objective scenes.

Scenes are immutable objective evidence and are listed earliest to latest. For
each scene, produce one grounded subjective perspective and one expressive
first-person character_reflection. Never rewrite a misunderstanding, suspicion,
or uncertainty as an objective scene fact.

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
    "behavioural_axes": {"axis_name": value_0_100},
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
effect is not warranted. Do not create or update profile state.

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
    "emotions":[{"arousal":0..100,"valence":-100..100,"description":"..."}],
    "beliefs":[{"statement":"...","confidence":0..100,"status":"suspected | believed | confirmed | doubted | disproven | superseded"}],
    "impacts":[{"impact_type":"goal_change | aspect_change","target_id":"supplied stable id","direction":"advanced | threatened | created | reinforced | invalidated","magnitude":0..100,"description":"..."}]
  }]
}

Return JSON only. required_output is authoritative if this description and the schema differ."""


OBSERVATIONS_PROMPT = r"""You are distilling character observations from canonical scene bundles.

Given canonical scenes, grounded interpretations, and immediate psychological enrichment, produce grounded
observations. Every observation must cite at least one scene evidence_id from the perspectives.
Expressive character_reflection text is presentation-only and is never supplied as evidence.

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

Return JSON only. required_output is authoritative if this description and the schema differ."""

PROFILE_UPDATE_PROMPT = r"""You are applying one chronological source's observations to a character's persistent profile.

Return one atomic update covering behavioural axes, aspects, and goals. The current profile already
contains updates from every earlier source. Default to retaining it unless the observations provide
clear evidence for a change.

BEHAVIOURAL AXES:
- calm_aggressive: 0 calm, 100 aggressive
- cautious_reckless: 0 cautious, 100 reckless
- compassionate_ruthless: 0 compassionate, 100 ruthless
- trusting_suspicious: 0 trusting, 100 suspicious
- honest_deceptive: 0 honest, 100 deceptive
- patient_impulsive: 0 patient, 100 impulsive
- humble_proud: 0 humble, 100 proud
- cooperative_dominating: 0 cooperative, 100 dominating

Return every axis exactly once. Use the current value when evidence does not justify a change.
Any changed value must remain within 5 points of its current value.

An aspect is a stable, high-impact identity fact, role, state, physical characteristic, capability,
knowledge, preference, attitude, or history. A goal is an active persistent driver, not a completed
one-time action.

INPUT:
{
  "current_profile": {
    "behavioural_axes": {"axis_name": 0..100},
    "aspects": [
      {"name": "name", "category": "category", "description": "text or null", "importance": 1..5, "intensity": 0..100 or null}
    ],
    "goals": [
      {"title": "title", "description": "text", "goal_type": "type", "priority": 0..100, "commitment": 0..100}
    ]
  },
  "observations": {
    "recurring_behaviours": [...],
    "motivations": [...],
    "values": [...],
    "fears": [...],
    "conflicts": [...],
    "relationships": [...],
    "contradictions": [...],
    "evidence_gaps": [...]
  },
  "allowed_evidence_ids": ["scene:exact-scene-id"],
  "limits": {"max_aspects": 0..50, "max_goals": 0..50},
  "required_output": "<ProfileUpdateOutput schema>"
}

OUTPUT:
{
  "behavioural_axes": [
    {
      "axis": "one of the eight exact axis names",
      "new_value": 0..100,
      "justification": "why this value is retained or changed",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ],
  "aspect_updates": [
    {
      "operation": "add | update | remove",
      "name": "aspect name",
      "category": "identity | role | status | physical | capability | knowledge | preference | attitude | history",
      "description": "description or null",
      "importance": 1..5 or null,
      "intensity": 0..100 or null,
      "justification": "why this operation",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ],
  "goal_updates": [
    {
      "operation": "add | update | remove | complete",
      "title": "goal title",
      "description": "description or null",
      "goal_type": "desire | objective | ambition | obligation | avoidance | survival",
      "priority": 0..100 or null,
      "commitment": 0..100 or null,
      "basis": "explicit | inferred",
      "justification": "why this operation",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ]
}

For remove or complete operations, the stable name/title and justification are required; fields that
describe the resulting active item are ignored. Every evidence_ids value must
come exactly from allowed_evidence_ids. Return JSON only."""
