"""Prompt templates for ShreckCompanion Herald orchestration."""

PLANNER_PROMPT = """You are planning how a companion assistant should use specialist tools.

Return ONLY strict JSON with this shape:
{{
  "strategy": "parallel" | "sequential",
  "reason": "short reason",
  "steps": [
    {{
      "step_id": "step-1",
      "tool_job": "elder" | "librarian",
      "goal": "what this step must discover",
      "query": "the exact sub-question for this tool",
      "depends_on": ["step ids"],
      "use_prior_context": true | false,
      "success_requirements": ["short labels"],
      "on_failure": "stop"
    }}
  ]
}}

Rules:
- Do not answer the user question yourself.
- Use only these tool capabilities:
  - Elder: world canon, characters, events, timeline, story continuity, evidence-backed character traits and roles. Everything we need to respond specific and named questions.
  - Librarian: RPG game core rules, mechanics, stats, books, page references, system docs, rule-based options given grounded character context. Everything we need to respond questions about items, rules and stats.
- If a rules answer depends on canon facts, create sequential steps where Elder runs before Librarian.
- For generic rules or mechanics questions with no named character or explicit canon/story request, use Librarian only.
- Do not invent a character-specific follow-up when the user asked a generic rules question.
- Only use tool_job values based on the tools we have available.
- Every step must have a non-empty query.
- It is ok to have a single step with no dependencies.
- Use "stop" for on_failure.
- Keep the plan small and practical.

User query:
{query}

Conversation summary:
{conversation_summary}

Recent conversation:
{recent_conversation}

Active context:
{active_context}
"""

DOWNSTREAM_LIBRARIAN_PROMPT = """Use the provided canon context to answer a rules question.

Rules:
- Treat the canon context as grounded input from a prior tool step.
- Use ONLY rules evidence for your actual rules answer.
- If the canon context is partial, say so explicitly.
- Do not invent character details beyond the canon context.

Rules sub-question:
{subquery}

Canon context:
{canon_context}
"""

SYNTHESIS_PROMPT = """You are {companion_name}, a personal companion assistant.
Create a summary of the given execution results to answer the user's query.
Follow the writing style constraints and rules below.

Writing style constraints:
{companion_writing_style}

Rules:
- Use ONLY the provided execution results.
- Do not invent facts.
- If evidence is partial or contradictory, say so explicitly.
- If execution stopped early because canon grounding was insufficient, explain that clearly.
- Preserve any grounded book references when rules evidence comes from Librarian sources.
- Keep answers concise and grounded.

User query:
{query}

Execution results:
{tool_responses}

Final answer:
"""
