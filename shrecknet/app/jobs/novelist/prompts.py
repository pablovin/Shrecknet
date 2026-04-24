"""Prompt set for the scene-centric Novelist pipeline."""

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

NOVELIST_SCENE_PACKAGE_PROMPT = """You are converting a normalized scene scaffold into a scene writing package.

Task:
For each input scene, produce a package with these fields:
- scene_id
- source_paragraphs
- raw_scene_text
- scene_summary
- scene_goal
- milestones
- related_entities
- temporal_position_hint
- tone_hint
- open_questions_for_retrieval

Strict rules:
- Output JSON only.
- Keep open_questions_for_retrieval short and concrete.
- Do not rewrite raw_scene_text.

Return ONLY valid JSON in this exact shape:
{
  "scene_packages": [
    {
      "scene_id": "scene-001",
      "source_paragraphs": [1, 2, 3],
      "raw_scene_text": "...",
      "scene_summary": "...",
      "scene_goal": "...",
      "milestones": ["..."],
      "related_entities": ["..."],
      "temporal_position_hint": "early|middle|late",
      "tone_hint": "...",
      "open_questions_for_retrieval": ["..."]
    }
  ]
}"""

NOVELIST_ELDER_QUERY_PROMPT = """You are generating scene-local Elder retrieval questions.

Task:
- Create 2 to 4 concrete retrieval questions for this scene package.

Strict rules:
- Questions must target continuity and dramatic relevance only.
- Do not ask Elder to invent new plot events.
- Questions should help retrieve prior events, relationships, personality cues, unresolved tensions, style details, and contradiction risks.

Return ONLY valid JSON in this exact shape:
{
  "queries": ["..."]
}"""

NOVELIST_SCENE_INTENT_PROMPT = """You are drafting a compact scene intent.

Task:
- Describe what must happen in this scene before prose generation.

Return ONLY valid JSON in this exact shape:
{
  "scene_id": "scene-001",
  "what_happens": ["..."],
  "emotional_progression": ["..."],
  "speaking_goals": ["..."],
  "implied_history": ["..."],
  "forbidden_contradictions": ["..."]
}"""

NOVELIST_SCENE_PROSE_PROMPT = """You are writing one scene in third-person prose.

Strict rules:
- Write exactly 1 paragraph plus 1 dialogue block when possible.
- Keep dialogue and narration balanced.
- Keep third-person perspective.
- Return valid HTML only, using <p> and <blockquote> only.
- DO NOT output markdown or any text outside HTML.
"""

NOVELIST_SCENE_CRITIC_PROMPT = """You are a structural critic over one complete chapter draft.

Task:
- Inspect continuity, duplication, transitions, voice, pacing, contradictions, and exposition balance.
- Do not rewrite prose.

Return ONLY valid JSON in this exact shape:
{
  "global_notes": ["..."],
  "by_scene": {}
}"""

NOVELIST_SCENE_REVISION_PROMPT = """Revise the full draft using critic feedback.

Strict rules:
- Output only revised prose HTML.
- Use only <p> and <blockquote>.
- Keep continuity and voice consistent.

Return HTML only."""
