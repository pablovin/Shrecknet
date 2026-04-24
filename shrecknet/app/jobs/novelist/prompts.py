"""Prompt set for the scene-centric Novelist pipeline.

Step mapping (orchestrator):
- continuity_brief: CONTINUITY_BRIEF_PROMPT
- step_1_scaffolding: deterministic (no novelist LLM prompt)
- step_2_scene_exploration: NOVELIST_SCENE_EXPLORATION_PROMPT
- step_3_scene_context_creation: NOVELIST_SCENE_CONTEXT_CREATION_PROMPT
- step_4_scene_intent: NOVELIST_SCENE_INTENT_PROMPT
- step_5_scene_prose: NOVELIST_SCENE_PROSE_PROMPT
- step_6_scene_critic: NOVELIST_SCENE_CRITIC_PROMPT
- step_7_scene_revision: NOVELIST_SCENE_REVISION_PROMPT
"""

# Prompt inventory for quick lookup in logs/debug UIs.
NOVELIST_PROMPT_BY_STEP = {
    "continuity_brief": "CONTINUITY_BRIEF_PROMPT",
    "step_1_scaffolding": "deterministic_no_prompt",
    "step_2_scene_exploration": "NOVELIST_SCENE_EXPLORATION_PROMPT",
    "step_3_scene_context_creation": "NOVELIST_SCENE_CONTEXT_CREATION_PROMPT",
    "step_4_scene_intent": "NOVELIST_SCENE_INTENT_PROMPT",
    "step_5_scene_prose": "NOVELIST_SCENE_PROSE_PROMPT",
    "step_6_scene_critic": "NOVELIST_SCENE_CRITIC_PROMPT",
    "step_7_scene_revision": "NOVELIST_SCENE_REVISION_PROMPT",
}

# Continuity context prompt (previous session summary helper).
CONTINUITY_BRIEF_PROMPT = """You are extracting compact continuity context from the previous session text.

Output requirements:
- Return exactly 5 to 8 short lines.
- Each line must be compact and factual.
- Focus ONLY on:
  - key characters present
  - relationships or tensions
  - unresolved threads
  - emotional tone at the end of the session

Strict rules:
- DO NOT include full narration.
- DO NOT invent anything.
- DO NOT include new events.
- Keep it compact and factual.
- If information is missing, omit it; do not fabricate.
- Frontend instructions are authoritative for spelling/normalization of names and terms.
- If frontend instructions provide corrected names/labels, always use those corrected forms.
- Return plain text lines only."""

# Step 1: scaffolding normalization (legacy, currently unused in pipeline).
NOVELIST_SCAFFOLD_NORMALIZATION_PROMPT = """You are normalizing Architect-derived scene scaffolding for a scene-centric novelist pipeline.

Task:
1) Review the proposed scenes, milestones, and related entities.
2) Keep strict chronological order.
3) Mark each scene as "new" or "update".
4) Keep source anchors for traceability.

Strict rules:
- Output JSON only.
- Do not invent scenes or entities not supported by source anchors.
- Preserve scene identifiers when possible.

Return ONLY valid JSON in this exact shape:
{
  "scenes": [
    {
      "scene_id": "scene-001",
      "name": "...",
      "scene_summary": "...",
      "milestones": ["..."],
      "related_entities": ["..."],
      "source_anchors": ["P1-P4"],
      "new_or_update": "new"
    }
  ]
}"""

# Step 2: scene package exploration.
NOVELIST_SCENE_EXPLORATION_PROMPT = """You are exploring one scene to produce only missing planning details.

Task:
For the given scene, produce ONLY:
- prior_knowledge_needed (max 5 items, each with question+answer)
- scene_tone (one short paragraph)
- scene_goal

Strict rules:
- Output JSON only.
- Do not repeat fields already provided by input.
- Questions must ask for prior knowledge useful to write this scene.
- Keep questions concrete and continuity-focused.
- Do not invent events outside the scene context.

Return ONLY valid JSON in this exact shape:
{
  "scene_id": "scene-001",
  "prior_knowledge_needed": [
    {"question": "...", "answer": "..."}
  ],
  "scene_tone": "...",
  "scene_goal": "..."
}"""

# Step 3: retrieval-informed narrative context creation.
NOVELIST_SCENE_CONTEXT_CREATION_PROMPT = """You are creating focused narrative context from scene data and retrieved Q/A evidence.

Task:
Using only the provided scene payload and the provided questions/answers, output ONLY:
- prior_events
- relationship_summaries
- personality_reminders
- unresolved_tensions
- style_details
- contradiction_warnings

Strict rules:
- Output JSON only.
- Each field should be a short but detailed paragraph.
- Do not invent unsupported facts.
- If evidence is weak for one field, still provide the best grounded compact summary.

Return ONLY valid JSON in this exact shape:
{
  "prior_events": "...",
  "relationship_summaries": "...",
  "personality_reminders": "...",
  "unresolved_tensions": "...",
  "style_details": "...",
  "contradiction_warnings": "..."
}"""

# Step 4: scene intent drafting.
NOVELIST_SCENE_INTENT_PROMPT = """You are drafting a compact scene intent.

Task:
- Describe what must happen in this scene before prose generation.

Return ONLY valid JSON in this exact shape:
{
  "what_happens": ["..."],
  "emotional_progression": ["..."],
  "speaking_goals": ["..."],
  "implied_history": ["..."],
  "forbidden_contradictions": ["..."]
}"""

# Step 5: scene prose generation.
NOVELIST_SCENE_PROSE_PROMPT = """You are writing one scene in third-person prose.

Strict rules:
- Output full HTML only.
- Use only <p> and <blockquote> tags.
- Write one strong scene passage with narration, description, tension, and dialogue when needed.
- Keep third-person perspective.
- DO NOT output markdown, JSON, or commentary.
- Output only the scene HTML.
"""

# Step 6: full-draft critic pass.
NOVELIST_SCENE_CRITIC_PROMPT = """You are a structural critic over one complete chapter draft.

Task:
- Inspect continuity, duplication, transitions, voice, pacing, contradictions, and exposition balance.
- Do not rewrite prose.

Return ONLY valid JSON in this exact shape:
{
  "global_notes": ["..."],
  "by_scene": {}
}"""

# Step 7: full-draft revision pass.
NOVELIST_SCENE_REVISION_PROMPT = """Revise the full draft using critic feedback.

Strict rules:
- Output only revised prose HTML.
- Use only <p> and <blockquote>.
- Keep continuity and voice consistent.

Return HTML only."""
