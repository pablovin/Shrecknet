"""Librarian prompts in runtime pipeline order.

Call sites live in ``query_v2.py``. Retrieval itself is deterministic/model-
based and therefore has no LLM prompt between planning and validation.
"""

# ---------------------------------------------------------------------------
# Step 1: information-needs planning
# Used by: LibrarianQueryV2._plan
# Inputs: original user query and all RPG systems attached to the agent.
# Expected output: JSON object with ``information_needs`` containing 1-8
# standalone search questions. Those questions become v2 retrieval queries.
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

Return exactly one JSON object in this format:
{{
  "information_needs": [
    "First standalone search question",
    "Second standalone search question"
  ]
}}

- Return between 1 and 8 strings in `information_needs`.
- Every array item must be a non-empty standalone search question.
- Do not add any keys other than `information_needs`.
- Return raw valid JSON only: no Markdown fence, commentary, introduction, or trailing text.
"""


def planner_messages(*, query: str, rpg_system: str) -> list[dict[str, str]]:
    """Build the system/user message pair consumed by the planning LLM call."""
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": PLANNER_USER_PROMPT.format(query=query, rpg_system=rpg_system)},
    ]


# ---------------------------------------------------------------------------
# Step 2: evidence coverage validation
# Used by: LibrarianQueryV2._validate after every retrieval pass.
# Inputs: original question, complete initial information-needs plan, and the
# accumulated source-labelled display evidence (never embedding_text).
# Expected output: JSON with ``adequate``, ``covered_needs``, ``missing_needs``,
# and ``reason``. Novel missing needs drive the next retrieval pass; adequate
# evidence or an empty/repeated missing list stops the loop.
# Malformed JSON is checked and sent to the same shared JSON-repair service;
# validation fails safe only if parsing and repair both fail.
# ---------------------------------------------------------------------------

VALIDATOR_SYSTEM_PROMPT = "You are validating whether retrieved book evidence is sufficient to answer an RPG rules question."

VALIDATOR_USER_PROMPT = """User Question: {query}
Required information: {needs_json}
Retrieved Evidence:
{evidence}

Mark adequate only when the evidence supports the complete answer. Do not use outside knowledge.

Return exactly one JSON object in this format:
{{
  "adequate": false,
  "covered_needs": [
    "Required information need already supported by the evidence"
  ],
  "missing_needs": [
    "Standalone search question for information still missing"
  ],
  "reason": "Brief explanation of why the evidence is or is not adequate"
}}

- `adequate` must be a JSON boolean, never a string.
- `covered_needs` must be an array of strings. Use an empty array when none are covered.
- `missing_needs` must be an array of independently searchable strings. It must be empty when `adequate` is true.
- `reason` must be a non-empty string grounded only in the supplied evidence.
- Do not add any other keys.
- Return raw valid JSON only: no Markdown fence, commentary, introduction, or trailing text."""


def validator_messages(*, query: str, needs_json: str, evidence: str) -> list[dict[str, str]]:
    """Build the system/user message pair consumed by each validation call."""
    return [
        {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
        {"role": "user", "content": VALIDATOR_USER_PROMPT.format(
            query=query, needs_json=needs_json, evidence=evidence
        )},
    ]


# ---------------------------------------------------------------------------
# Step 3a: optional model prewarm
# Used by: LibrarianQueryV2._prewarm immediately before synthesis, only for a
# configured Ollama target whose previous warmup is older than five minutes.
# Expected output: ignored. This is an operational latency warmup, not evidence
# and not part of the answer or trace semantics.
# ---------------------------------------------------------------------------

MODEL_PREWARM_PROMPT = "ping"


# ---------------------------------------------------------------------------
# Step 3b: final grounded synthesis
# Used by: LibrarianQueryV2._synthesize for ``nl`` and ``both`` modes.
# Inputs: original question, RPG systems, consolidated source-labelled book
# excerpts, and the agent's writing style/personality.
# Expected output: the final answer with every supported statement wrapped as
# ``[text]{cite source_id=source-N}``. citations.py resolves those stable IDs
# into trusted title/page links and derives ``sources_used``.
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = """You are an expert librarian for the {rpg_system} RPG system.

The user made a question, and you need to answer using only the supplied book evidence. Do not use outside knowledge or invent missing rules.

Every factual claim derived from the evidence must be wrapped as:
[text]{{cite source_id=source-N}}

Use only source identifiers present in the evidence and copy them exactly. Cite each factual sentence or table cell; do not wrap purely stylistic transitions or headings.

Preserve conflicting rules and source-specific qualifications. Never claim that something does not exist merely because it was not retrieved. If evidence is insufficient, state what cannot be established.

Apply the requested writing style only to presentation. It must not alter, exaggerate, or omit factual content."""



SIMPLIFIED_ANSWER_STYLE_PROMPT = """**Question**
{query}

**Book evidence**
{chunks}

**Evidence validation**
{validation_result}

**Writing style**
{writing_style}

Produce a clear answer using only the book evidence.

Requirements:
- Directly answer the question before adding optional explanation.
- Cite every sourced factual sentence as [text]{{cite source_id=source-N}}.
- Use only source IDs provided in the evidence.
- Preserve exact numbers, terminology, exceptions, prerequisites, and conflicts.
- Use lists or Markdown tables when they improve clarity.
- Do not infer that a rule or entry does not exist unless the evidence is explicitly marked exhaustive.
- If validation found missing evidence, answer the supported portion and clearly identify what remains unsupported.
- Do not mention retrieval, chunks, the validator, or internal processing unless explaining an evidence limitation.

**Answer**"""


# Appended to the synthesis user prompt only when validation did not establish
# adequate coverage. The model must use it to disclose unsupported or missing
# information; it must not treat the warning as book evidence.
EVIDENCE_WARNING_PROMPT = """The available evidence was not validated as complete:

{warning}

Answer only the supported portion. Clearly state which part of the question cannot be established from the supplied evidence. Do not fill gaps with general knowledge or treat missing evidence as proof of absence."""
