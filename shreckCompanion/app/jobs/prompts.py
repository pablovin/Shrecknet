"""Prompt templates for ShreckCompanion Herald orchestration."""

COMPANION_POLICY_PROMPT = """You are planning companion lifecycle policy for a single turn.

Return ONLY strict JSON with this shape:
{{
  "chat_goal": "short statement",
  "turn_intention": "what this turn should accomplish",
  "conversation_mode": "label",
  "user_need": "label",
  "needs_knowledge_tools": true | false,
  "suggested_response_style": {{
    "directness": 0.0,
    "technical_depth": 0.0,
    "playfulness": 0.0,
    "initiative": 0.0
  }},
  "open_threads": ["thread"],
  "next_best_actions": ["action"]
}}

Rules:
- Keep values practical and concise.
- Use numbers in range [0.0, 1.0] for style values.
- Do not invent canon or rules evidence.
- This policy guides behavior only; it does not answer the user question.

User query:
{query}

Conversation summary:
{conversation_summary}

Recent conversation:
{recent_conversation}

Conversation context:
{active_context}

Current chat state:
{chat_state}

Current rapport profile:
{rapport_profile}
"""

PLANNER_PROMPT = """You are planning how a companion assistant should use specialist tools.

Return ONLY strict JSON with this shape:
{{
  "needs_tools": true | false,
  "no_tools_reason": "short reason when needs_tools=false",
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
- First decide if any tool is needed at all for this question.
- If no tools are needed, set needs_tools=false and return an empty steps array.
- If tools are needed, set needs_tools=true and produce a minimal viable plan.
- Use only these tool capabilities:
  - Elder: world canon, characters, events, timeline, story continuity, evidence-backed character traits and roles. Everything we need to respond specific and named questions.
  - Librarian: RPG game core rules, mechanics, stats, books, page references, system docs, rule-based options given grounded character context. Everything we need to respond questions about items, rules and stats.
- For character/canon/lore identity questions (for example: "Is Ernst a human?"), use Elder only.
- Use Librarian only for RPG rules/mechanics/books/stat questions.
- Do not call Librarian for pure lore questions.
- Do not create hard dependencies from Librarian to Elder.
- If Elder context would help a Librarian step, set use_prior_context=true for Librarian, but keep depends_on empty and allow Librarian to run even if no Elder result is available.
- For generic rules or mechanics questions with no named character or explicit canon/story request, use Librarian only.
- Do not invent a character-specific follow-up when the user asked a generic rules question.
- Only use tool_job values based on the tools we have available.
- Every step must have a non-empty query.
- It is ok to have a single step with no dependencies.
- The first step must set use_prior_context=false.
- For non-Librarian steps, if use_prior_context=true, depends_on must include at least one earlier step_id.
- Use "stop" for on_failure.
- Keep the plan small and practical.

Companion policy summary:
{companion_policy}

Available tools for this session:
{available_tools}

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

TURN_REFLECTION_PROMPT = """You are evaluating a completed companion turn.

Return ONLY strict JSON with this shape:
{{
  "answered_user": true | false,
  "confidence": 0.0,
  "user_state_estimate": {{
    "engagement": "low|medium|high",
    "frustration": "low|medium|high",
    "confusion": "low|medium|high",
    "boredom": "low|medium|high"
  }},
  "response_quality": {{
    "too_verbose": true | false,
    "too_dry": true | false,
    "missed_question": true | false,
    "needs_more_concrete_next_step": true | false
  }},
  "proactivity": {{
    "should_be_proactive": true | false,
    "proactivity_type": "none|suggest_next_step|ask_clarifying_question|surface_unresolved_thread|warn_about_risk|offer_alternative_direction|connect_to_prior_context|summarize_progress",
    "proactive_message": "optional short sentence"
  }},
  "chat_state_patch": {{
    "chat_goal": "optional",
    "current_intention": "optional",
    "open_threads_add": ["thread"],
    "open_threads_resolved": ["thread"],
    "next_best_actions": ["action"]
  }},
  "rapport_patch": [
    {{
      "trait": "directness|technical_depth|playfulness|initiative|question_frequency|creative_suggestion_frequency|emotional_support",
      "delta": 0.0,
      "confidence": 0.0,
      "reason": "short reason"
    }}
  ]
}}

Rules:
- Use deltas in range [-1.0, 1.0].
- Do not modify core personality.
- If confidence is low, prefer empty rapport_patch.
- Keep proactive_message short and non-repetitive.

User query:
{query}

Companion response:
{final_text}

Execution summary:
{execution_summary}

Current chat state:
{chat_state}

Current rapport profile:
{rapport_profile}
"""
