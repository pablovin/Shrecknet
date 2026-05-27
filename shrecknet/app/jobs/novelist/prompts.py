"""Prompt set for the scene-centric Novelist pipeline.

Step mapping (orchestrator):
- step_2_scene_exploration: NOVELIST_SCENE_EXPLORATION_PROMPT
- step_3_scene_context_creation: NOVELIST_SCENE_CONTEXT_CREATION_PROMPT
- step_4_scene_intent: NOVELIST_SCENE_INTENT_PROMPT
- step_5_scene_prose: NOVELIST_SCENE_PROSE_PROMPT
- step_6_scene_critic: NOVELIST_SCENE_CRITIC_PROMPT
- step_7_scene_revision: NOVELIST_SCENE_REVISION_PROMPT
"""

NOVELIST_SCENE_MERGE_PROMPT = """You are merging adjacent narrative scenes into one stronger scene.

Task:
- Given a bundle of adjacent scenes, produce one merged scene title and one merged scene summary.
- Keep chronology intact and preserve key entities/conflicts from all input scenes.
- Avoid generic/vague names.

Return ONLY valid JSON in this shape:
{
  "name": "...",
  "scene_summary": "..."
}
"""

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
- Create a maximum of three paragraphs of text, nothing more than that!
"""

# Step 6: full-draft critic pass.
NOVELIST_SCENE_CRITIC_PROMPT = """You are a structural critic over one complete chapter draft.

Task:
- Inspect continuity, duplication, transitions, voice, pacing, contradictions, and exposition balance across the full chapter.
- Do not rewrite prose.
- Provide editorial feedback that can drive a full rewrite pass.
- `by_scene` keys MUST be scene titles exactly as they appear in `<h1>...</h1>` blocks.

Return ONLY valid JSON in this exact shape:
{
  "global_notes": ["..."],
  "by_scene": {
    "Scene Title": {
      "continuity_issues": ["..."],
      "duplication": ["..."],
      "missing_transitions": ["..."],
      "voice_drift": ["..."],
      "pacing": ["..."],
      "graph_contradictions": ["..."],
      "exposition_problems": ["..."]
    }
  }
}"""

# Step 7: full-draft revision pass.
NOVELIST_SCENE_REVISION_PROMPT = """Re-write the full text using critic feedback.

Strict rules:
- Output only revised prose HTML.
- Use only <h1>, <p> and <blockquote>.
- Keep continuity and voice consistent.
- Preserve scene boundaries and keep each scene title in an <h1> block.
- Rewrite the complete text using the critic notes.
- Add dialogues when needed to enhance character dynamics.

Return HTML only."""
