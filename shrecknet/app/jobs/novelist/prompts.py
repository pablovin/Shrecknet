"""Prompt set for the v2 Novelist pipeline."""

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

NOVELIST_V2_MERGED_CHUNK_CONTEXT_PROMPT = """You are synthesizing elder-grounded context for one merged narrative chunk.

Task:
- Use ONLY the merged chunk payload and retrieved elder Q/A.
- Produce compact writing context focused on continuity, character dynamics, tensions, and style cues.

Return ONLY valid JSON in this exact shape:
{
  "prior_events": "...",
  "relationship_summaries": "...",
  "personality_reminders": "...",
  "unresolved_tensions": "...",
  "style_details": "...",
  "contradiction_warnings": "..."
}"""

NOVELIST_V2_MERGED_CHUNK_DRAFT_PROMPT = """You are writing one merged narrative chunk in third-person prose.

Strict rules:
- Output full HTML only.
- Use only <p> and <blockquote> tags.
- Keep chronology of the merged chunk.
- Output a maximum of three paragraphs total.
- Include at least one line of spoken dialogue when characters are present.
- Make character choices explicit: what is chosen, what is risked, and why now.
- Build tension inside the chunk (pressure, stakes, uncertainty), not flat summary.
- Use emotionally grounded narration (fear, resolve, doubt, desire) tied to concrete actions.
- No markdown, no JSON, no commentary.
"""

NOVELIST_V2_FINAL_REWRITE_PROMPT = """Re-write the full chapter from merged-chunk draft prose and critic notes.

Strict rules:
- Output only revised prose HTML.
- Use only <h1>, <p> and <blockquote>.
- Build clear narrative scaffolding across the full text: intro, climax, conclusion.
- Add smooth transitions between chunk content.
- Keep chronology and continuity consistent.
- Preserve scene separation in HTML: every scene MUST start with an <h1>Scene Title</h1> block followed by scene prose.
- Make transitions carry dramatic causality (the next scene should feel caused by prior choices/events).
- Strengthen dialogue cadence and subtext; avoid exposition-only narration.
- Emphasize character decisions and consequences in each scene.
- Keep emotional intensity progressive toward climax, then release into conclusion.
"""
