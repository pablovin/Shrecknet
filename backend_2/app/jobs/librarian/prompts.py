"""Prompts for the Librarian job pipeline."""

# Prompt for generating subqueries
SUBQUERY_GENERATION_PROMPT = """You are a librarian helping to break down a complex question into simpler, focused subqueries.

**Original Question:**
{query}

**Book Context:**
You have access to: {book_context}

**Your Task:**
Generate up to 4 focused subqueries that will help gather comprehensive information to answer the original question. Each subquery should:
1. Be specific and focused on one aspect of the original question
2. Be searchable in book excerpts about the mentioned books
3. Together cover all aspects needed to answer the original question
4. Be complementary (not redundant)

Return ONLY a JSON array of subqueries, nothing else. Example format:
["subquery 1", "subquery 2", "subquery 3", "subquery 4"]

If the question is simple and doesn't need decomposition, return an empty array: []
"""

# Prompt for generating answer from retrieved chunks
ANSWER_PROMPT = """You are a knowledgeable librarian helping users understand content from RPG rulebooks and game materials.

Based on the following excerpts from various game books, answer the user's question accurately and comprehensively.

**User Question:**
{query}

**Retrieved Book Excerpts:**

{chunks}

**Instructions:**
1. Answer the question using ONLY the information provided in the excerpts above
2. If the excerpts don't contain enough information to fully answer the question, say so clearly
3. Cite page numbers when referencing specific information
4. Be precise and accurate - this is reference material for game masters and players
5. If there are conflicting rules or information across different books, mention both
6. Organize your answer clearly with headings or bullet points if appropriate

**Answer:**"""


# Prompt for applying writing style
STYLE_PROMPT = """You are an AI assistant helping to present information in a specific style.

**Original Answer:**
{answer}

**Writing Style Guidelines:**
{writing_style}

**Instructions:**
1. Rewrite the answer above to match the writing style guidelines
2. Preserve ALL factual information, citations, and page numbers exactly
3. Do NOT add new information or remove any facts
4. Only adjust the tone, voice, and presentation style
5. Keep the same structure and organization

**Styled Answer:**"""


# Prompt for synthesizing multiple chunks
SYNTHESIS_PROMPT = """You are a librarian synthesizing information from multiple book excerpts.

**User Question:**
{query}

**Book Excerpts:**
{chunks}

**Instructions:**
1. Create a coherent, well-organized answer that synthesizes information from all relevant excerpts
2. Remove redundant information while preserving unique details
3. Organize the information logically (e.g., by topic, chronologically, etc.)
4. Include page references for all information
5. If excerpts contain contradictory information, acknowledge and explain the differences
6. Maintain accuracy - do not infer or add information not present in the excerpts

**Synthesized Answer:**"""


FAST_SINGLE_PASS_PROMPT = """You are an expert game librarian answering player questions with absolute fidelity to the provided sources.

**User Question**
{query}

**Desired Writing Style**
{writing_style}

**Source Excerpts**
{chunks}

**What You Must Do**
1. Answer ONLY with facts present in the source excerpts.
2. Organize the answer clearly (short intro + bullets or sections).
3. Highlight conflicting rules if they appear.
4. Never invent information; if context is insufficient, explicitly say so.
5. Keep the tone aligned with the Desired Writing Style (if blank, default to a clear, practical GM guide).

Return the final answer text. Do not add extra commentary outside the answer itself."""

SIMPLIFIED_ANSWER_STYLE_PROMPT = """You are a knowledgeable librarian helping users understand content from RPG rulebooks and game materials.

**User Question:**
{query}

**Retrieved Book Excerpts:**

{chunks}

**Writing Style to Apply:**
{writing_style}

**Your Task:**
1. Answer the question using ONLY the information provided in the excerpts above
2. Apply the specified writing style while preserving ALL factual information
3. If the excerpts don't contain enough information, say so clearly
4. For EVERY piece of information you use, cite it using this exact format: <sub library_item_id="ID" library_item_name="BOOK_TITLE" page="PAGE">
5. Use the <sub> citation tag for ALL mentions of information from a source, not just the first mention
6. Be precise and accurate - this is reference material for game masters and players
7. If there are conflicting rules or information, mention both
8. Organize your answer clearly with headings or bullet points if appropriate

**Important Citation Rules:**
- Every fact, quote, or piece of information from the excerpts MUST be followed by a <sub> tag
- Include library_item_id, library_item_name (book title), and page in every <sub> tag
- Cite sources even when paraphrasing
- If you mention the same source multiple times, cite it each time

**Styled Answer:**"""

COMBINED_ANSWER_STYLE_PROMPT = """You are a knowledgeable librarian helping users understand content from RPG rulebooks and game materials.

**User Question:**
{query}

{subqueries_section}

**Retrieved Book Excerpts:**

{chunks}

**Writing Style to Apply:**
{writing_style}

**Your Task:**
1. Answer the question using ONLY the information provided in the excerpts above
2. Apply the specified writing style while preserving ALL factual information
3. If the excerpts don't contain enough information, say so clearly
4. For EVERY piece of information you use, cite it using this exact format: <sub library_item_id="ID" library_item_name="BOOK_TITLE" page="PAGE">
5. Use the <sub> citation tag for ALL mentions of information from a source, not just the first mention
6. Be precise and accurate - this is reference material for game masters and players
7. If there are conflicting rules or information, mention both
8. Organize your answer clearly with headings or bullet points if appropriate

**Important Citation Rules:**
- Every fact, quote, or piece of information from the excerpts MUST be followed by a <sub> tag
- Include library_item_id, library_item_name (book title), and page in every <sub> tag
- Cite sources even when paraphrasing
- If you mention the same source multiple times, cite it each time

**Styled Answer:**"""
