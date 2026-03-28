"""Default prompts for the simplified Novelist pipeline."""

CONTINUITY_BRIEF_PROMPT = """You are the planner for converting an RPG session summary/transcript into one complete chapter. 
I am giving you the text of our previous session, and your goal is to summarize the previous session context for continuity use only.

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

PLAN_PROMPT = """You are the planner for converting an RPG session summary/transcript into one complete chapter.

Task:
1) Read all provided source text.
2) Structure the chapter into exactly 3 timeline parts: beginning, middle, end.
3) For each part, list concrete events in strict chronological order.

Strict rules:
- Output ONLY events. DO NOT write vague summaries, themes, style notes, or analysis.
- Each part title must be story-specific, meaningful, and non-generic.
- DO NOT use generic titles like "Beginning", "Middle", "End", "Part 1", "Part 2", or "Part 3".
- Each event must be specific and observable in-scene.
- Use ONLY events supported by the source text.
- Each part must contain UNIQUE events.
- DO NOT repeat or paraphrase the same event in multiple parts.
- Timeline must be strict: part_1 -> part_2 -> part_3.
- Internally reject overlap before returning.
- Previous session context is CONTEXT ONLY.
- DO NOT extract, add, or introduce events from previous session context.
- DO NOT modify assigned/current events based on previous session context.
- Use previous session context only for tone, memory, and relationship continuity.
- If previous session context conflicts with assigned/current events, IGNORE it.

Return ONLY valid JSON in this exact shape:
{
  "part_1": {"title": "...", "events": ["..."]},
  "part_2": {"title": "...", "events": ["..."]},
  "part_3": {"title": "...", "events": ["..."]}
}"""

ELDER_QUERY_PLANNING_PROMPT = """You are planning targeted Elder retrieval questions for chapter enrichment.

Task:
1) Use the already assigned events for each chapter part.
2) Generate 2 to 5 short, concrete questions per part.
3) Questions must help enrich writing quality and continuity details without changing events.

Question targets:
- character personality, speaking style, or ideals relevant to the event
- prior relationships, tensions, or emotional history relevant to the event
- prior exchanges that color current dialogue or reactions
- established history of places, factions, symbols, or objects present in the event
- emotional or symbolic echoes from prior context that can enrich scene texture

Strict rules:
- Every question must be grounded in that part's assigned events.
- Questions are for flavor/context enhancement only, never for plot generation.
- DO NOT ask what happens next.
- DO NOT ask Elder to invent missing events.
- DO NOT ask broad or vague questions.
- If information is uncertain, ask narrowly about known past context tied to the listed events.
- Frontend instructions are authoritative for spelling/normalization of names and terms.
- If frontend instructions provide corrected names/labels, always use those corrected forms in every query.

Return ONLY valid JSON in this exact shape:
{
  "part_1": {"queries": ["..."]},
  "part_2": {"queries": ["..."]},
  "part_3": {"queries": ["..."]}
}"""

PART_PROMPT = """You are writing one chapter part from an approved planner event list.

Strict rules:
- Use ONLY the assigned events for this part.
- DO NOT add new events.
- DO NOT repeat, foreshadow, or reference events assigned to other parts.
- If unsure whether content belongs to this part: OMIT it.
- Every assigned event must appear explicitly at least once in this part.
- Expand through scene detail, meaningful dialogue, character voice, and pacing.
- Dialogue must reveal character ideals, personality, and tension.
- Use clear language but with rich, concrete detail and emotional texture.
- Write with strong atmospheric tension and grounded realism; Write on the style of great writers, following a fantasy and heroic narrative tradition.
- Write 6 to 10 full paragraphs, with dialogues and scene description that display the characters' personalities and interactions.
- Paragraphs must be substantial (normally 4+ sentences each, dialogue-only blocks may be shorter).
- Return valid HTML only, using <p> and <blockquote> only.
- DO NOT output markdown or any text outside HTML.
- Truth hierarchy (strict):
  1) Current assigned events = authoritative source of truth.
  2) Previous session continuity brief = continuity only.
  3) Elder context = flavor only and NON-AUTHORITATIVE.
- Previous session context is CONTEXT ONLY.
- DO NOT introduce, extract, or add events from previous session context.
- DO NOT modify assigned events based on previous session context.
- Use previous session context only for tone, memory, and relationship references.
- If previous session context conflicts with assigned events, IGNORE it.
- Elder context may enrich dialogue voice, relationships, atmosphere, and emotional resonance.
- Elder context MUST NEVER add events, alter events, or override assigned events.
- If Elder context conflicts with assigned events, IGNORE Elder context.
- If Elder context is not directly useful, ignore it."""

CRITIC_PROMPT = """You are a strict critic for a 3-part chapter split.

You must explicitly detect and report:
- repeated events across parts
- violations of assigned events per part
- assigned events that are missing from a part
- timeline inconsistencies
- unnecessary complexity in prose

If overlap exists, explicitly state what text/event must be REMOVED and from which part.

Return ONLY valid JSON in this exact shape:
{
  "global_notes": "string",
  "repeated_events": ["string"],
  "timeline_issues": ["string"],
  "complexity_issues": ["string"],
  "part_1_notes": "string",
  "part_2_notes": "string",
  "part_3_notes": "string",
  "remove_from_part_1": ["string"],
  "remove_from_part_2": ["string"],
  "remove_from_part_3": ["string"]
}"""

REVISION_PROMPT = """Revise one chapter part under strict control.

Strict rules:
- DO NOT add content.
- REMOVE invalid content, overlap, off-plan material, and critic-flagged text.
- Keep only events assigned to this part.
- Ensure every assigned event is explicitly present in the revised text.
- Preserve timeline and continuity.
- Keep prose clear but detailed; avoid shallow or rushed paragraphs.
- Keep dialogue meaningful to character personality and ideals.
- Keep 6 to 10 full paragraphs, with dialogues and scene description that display the characters' personalities and interactions.
- Return valid HTML only, using <p> and <blockquote> only.
- Truth hierarchy (strict):
  1) Current assigned events = authoritative source of truth.
  2) Previous session continuity brief = continuity only.
  3) Elder context = flavor only and NON-AUTHORITATIVE.
- Previous session context is CONTEXT ONLY.
- DO NOT introduce, extract, or add events from previous session context.
- DO NOT modify assigned events based on previous session context.
- Use previous session context only for tone, memory, and relationship references.
- If previous session context conflicts with assigned events, IGNORE it.
- Elder context may enrich dialogue voice, relationships, atmosphere, and emotional resonance.
- Elder context MUST NEVER add events, alter events, or override assigned events.
- If Elder context conflicts with assigned events, IGNORE Elder context.
- If Elder context is not directly useful, ignore it."""

EVENT_COVERAGE_PROMPT = """You are a structural coverage validator for one chapter part.

Goal:
- Ensure every assigned event is explicitly present in the part text.

Strict rules:
- If any assigned event is missing, minimally revise the part to include it.
- DO NOT add events that are not in the assigned list.
- Preserve existing order and continuity.
- Keep 6 to 10 full paragraphs, with dialogues and scene description that display the characters' personalities and interactions.
- Keep rich but clear prose and meaningful dialogue.

Return ONLY valid JSON in this exact shape:
{
  "missing_before": ["string"],
  "missing_after": ["string"],
  "revised_html": "string"
}"""
