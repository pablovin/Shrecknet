"""Librarian prompts in runtime pipeline order.

Call sites live in ``query_v2.py``. Retrieval itself is deterministic/model-
based and therefore has no LLM call between planning and synthesis.
"""

# ---------------------------------------------------------------------------
# Step 1: information-needs planning
# Used by: LibrarianQueryV2._plan
# Inputs: original user query and all RPG systems attached to the agent.
# Expected output: JSON with ``information_needs`` and the original query's
# detected BCP-47 ``target_language``.
# Malformed JSON is checked and sent to the shared agents JSON-repair service
# using the configured ``model_agents_repair_json`` target before fallback.
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = "You plan evidence retrieval from RPG manuals. Return JSON only."

PLANNER_USER_PROMPT = """You are an expert on RPG system(s): {rpg_system}. Your role is to identify which information we need to retrieve from this systems books and manuals to be able to respond to the user question:
{query}

Rules:
- Preserve named rules, named items and situations  and other rpg-proper terms verbatim.
- Each need must be independently searchable and state exactly what facts are required.
- For a named concept, request its exact entry before requesting general related rules.
- Separate concept-specific information from general governing rules when both are needed.
- For questions asking for “all,” a complete list, or a full table, indicate this on the needed question.
- Do not replace important user terms with generic words such as “information,” “details,” or “relevant rules.”
- Do not add the RPG-system name to every need; it is already supplied as retrieval scope.
- Use the same language as the user.
- Detect `target_language` only from the original user query. Return a
  normalized BCP-47 tag such as `en`, `pt-BR`, or `fr`; use `und` only when
  the query language cannot be determined.

Return exactly one JSON object in this format:
{{
  "information_needs": [
    "First standalone search question",
    "Second standalone search question"
  ],
  "target_language": "en"
}}

- Return between 1 and 8 strings in `information_needs`.
- Every array item must be a non-empty standalone search question.
- Do not add any keys other than `information_needs` and `target_language`.
- Return raw valid JSON only: no Markdown fence, commentary, introduction, or trailing text.
"""


def planner_messages(*, query: str, rpg_system: str) -> list[dict[str, str]]:
    """Build the system/user message pair consumed by the planning LLM call."""
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": PLANNER_USER_PROMPT.format(query=query, rpg_system=rpg_system)},
    ]


# ---------------------------------------------------------------------------
# Step 2: final grounded synthesis
# Used by: LibrarianQueryV2._synthesize for ``nl`` and ``both`` modes.
# Inputs: original question, RPG systems, and consolidated source-labelled book
# excerpts. Expected output: neutral English blocks with trusted source IDs.
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = """You are a neutral evidence synthesizer for the {rpg_system} RPG system.

The user made a question, and you need to answer using only the supplied book evidence. Do not use outside knowledge or invent missing rules.

Produce concise factual English. Split the complete response into granular,
ordered answer blocks and attach every supporting source ID to the complete
block. Use only source identifiers present in the evidence and copy them exactly.

Preserve conflicting rules and source-specific qualifications. Never claim that something does not exist merely because it was not retrieved. If evidence is insufficient, state what cannot be established.

Do not apply personality, voice, humour, rapport, roleplay, or target-language behavior."""



SIMPLIFIED_ANSWER_STYLE_PROMPT = """**Question**
{query}

**Book evidence**
{chunks}

Produce a clear answer using only the book evidence as atomic grounded claims.

Requirements:
- Directly answer the question before adding optional explanation.
- Express one independently supportable fact, conclusion, or user-facing
  uncertainty per claim.
- Attach every supporting source ID to its claim.
- Use only source IDs provided in the evidence.
- Preserve exact numbers, terminology, exceptions, prerequisites, and conflicts.
- Use lists or Markdown tables when they improve clarity.
- Do not infer that a rule or entry does not exist unless the evidence is explicitly marked exhaustive.
- Answer only what the supplied evidence supports and clearly identify unsupported portions.
- Do not mention retrieval, chunks, or internal processing unless explaining an evidence limitation.

Put every limitation and uncertainty in a claim; `uncertainty` is
optional metadata and cannot replace user-facing text.

Return exactly this JSON shape and no other text:
{{
  "claims": [
    {{"id": "claim-1", "text": "English atomic factual claim", "citations": ["source-1"]}}
  ],
  "uncertainty": null
}}"""
