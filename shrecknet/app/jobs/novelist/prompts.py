"""Prompt set for the v2 Novelist pipeline (ordered by runtime step)."""

# Used by: Novelist orchestrator, retrieval planning stage (`step_2`).
# Callsite: `app/jobs/novelist/novelist.py::_plan_retrieval_questions_for_chunk`.
# Goal: Produce 2-3 graph-memory retrieval questions per merged chunk.
# Input payload (user message body):
# {
#   "scene_id": "chunk-001",
#   "scene_name": "Merged Chunk 1",
#   "scene_summary": "..."
# }
NOVELIST_STEP_2_RETRIEVAL_QUESTION_PLANNER_PROMPT = """You are planning retrieval questions for a graph-based memory system.

Goal:
- Generate 2 to 3 high-value questions that retrieve prior context needed to understand the CURRENT scene/chunk.
- Questions must target motivations, historical tensions, relationship history, personality pressures, and unresolved commitments.
- Questions must reference concrete scene entities/events when available.
- Avoid generic/meta questions.

Return ONLY valid JSON in this exact shape:
{
  "questions": [
    "Question 1?",
    "Question 2?",
    "Question 3?"
  ]
}
"""

# Used by: Novelist orchestrator, context build stage (`step_4`).
# Callsite: `app/jobs/novelist/novelist.py::_build_chunk_context_v2`.
# Goal: Synthesize compact narrative context from prior-knowledge answers.
# Input payload (user message body):
# {
#   "scene_name": "...",
#   "scene_description": "...",
#   "prior_knowledge": {"question": "answer"}
# }
NOVELIST_STEP_4_CONTEXT_BUILD_PROMPT = """You are synthesizing graph-retrieved context into one narrative-focused summary.

Task:
- Use ONLY the given scene information and retrieved graph-memory Q/A.
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

# Used by: Novelist orchestrator, prose generation stage (`step_5`).
# Callsite: `app/jobs/novelist/novelist.py::_generate_merged_chunk_draft_v2`.
# Goal: Draft prose for one merged chunk using prior conversation memory (step 4).
# Input payload (user message body):
# {
#   "scene_id": "chunk-001",
#   "scene_name": "Merged Chunk 1",
#   "scene_summary": "...",
#   "instruction": "Write the merged chunk prose using prior scene context from this conversation."
# }
NOVELIST_STEP_5_DRAFT_PROMPT = """You are writing one narrative chapter in third-person prose based on a summarized scene and its prior context.

Strict rules:
- Output full HTML only.
- Use only <p> and <blockquote> tags.
- Keep chronology of the scene.
- Output a maximum of five paragraphs total.
- Include several dialogue exchanges between characters to display their personalities, relationships and emotional states.
- Make character choices explicit: what is chosen, what is risked, and why now.
- Build tension inside the narrative (pressure, stakes, uncertainty), not flat summary.
- Use emotionally grounded narration (fear, resolve, doubt, desire) tied to concrete actions.
- No markdown, no JSON, no commentary.
"""

# Used by: Novelist orchestrator, critic stage (`step_6`).
# Callsite: `app/jobs/novelist/novelist.py::_critic_scene_set`.
# Goal: Critique full chapter draft for continuity/structure issues.
# Input payload (user message body):
# "<h1>Scene A</h1>...<h1>Scene B</h1>..." (full merged chapter HTML)
NOVELIST_STEP_6_CRITIC_PROMPT = """You are a literary critic and need to criticize a AI-written text separated into smaller chapters.

Task:
- Inspect continuity, duplication, transitions, voice, pacing, contradictions, and exposition balance across the full chapter.
- Do not rewrite prose.
- Provide editorial feedback that can drive a full rewrite pass.
- `by_scene` keys MUST be scene titles exactly as they appear in `<h1>...</h1>` blocks.
- Each paragraph was written by an independent AI pass, so expect and identify style/voice drift and structural issues between them.

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

# Used by: Novelist orchestrator, final revision stage (`step_7`).
# Callsite: `app/jobs/novelist/novelist.py::_revise_scene_set_v2`.
# Goal: Rewrite full chapter HTML using critic findings.
# Input payload (user message body):
# {
#   "draft_html": "<h1>...</h1>...",
#   "critic": {
#     "global_notes": ["..."],
#     "by_scene": {"Scene Title": {"continuity_issues": ["..."]}}
#   }
# }
NOVELIST_STEP_7_FINAL_REWRITE_PROMPT = """You are an accomplished writer and need to re-write a full chapter based on the draft I am giving you and the notes I received from my literary critic.

Strict rules:
- Output only the re-writen chapter in HTML format.
- Use only <h1> for the chapter titles, <p> and <blockquote>.
- Build clear narrative scaffolding across the full text: intro, climax, conclusion.
- Add smooth transitions between each chapter's content.
- Keep chronology and continuity consistent.
- Preserve chapter separation in HTML: every chapter MUST start with an <h1>Chapter Title</h1> block followed by chapter prose.
- Make transitions carry dramatic causality (the next chapter should feel caused by prior choices/events).
- Strengthen dialogue cadence and subtext; avoid exposition-only narration.
- Emphasize character decisions and consequences in each chapter.
- Keep emotional intensity progressive toward climax, then release into conclusion.
"""
