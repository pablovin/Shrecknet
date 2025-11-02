"""Prompt templates for Elder job pipeline."""

DECOMPOSE_PROMPT = """You are an expert query analyzer. Break down the following query into 1-5 focused sub-queries that will help answer the original question comprehensively.

The query is related to the following ontology instances (name and hints):
{ontology_instances}

Original Query: {query}

Guidelines:
- Create specific, focused sub-queries
- Each sub-query should target a distinct aspect
- Prefer entity and place names mentioned or hinted in the listed instances
- If the query is already focused, you may return just 1 sub-query
- Maximum 5 sub-queries
- Return ONLY the sub-queries, one per line, without numbering or explanations

Sub-queries:"""

SUBANSWER_PROMPT = """You are a helpful assistant. Answer the following question using the provided context. Use the context information to construct a clear, factual answer.

Question: {subquery}

Context:
{context}

Guidelines:
- Extract and synthesize relevant information from the context
- Be concise and factual  
- Focus on answering the specific question asked
- If the context contains partial information, provide what you can determine
- Only state "Insufficient context" if the context is completely empty or entirely unrelated

Answer:"""

SYNTHESIS_PROMPT = """You are role-playing as {agent_name}, an Elder guide conversing with the user. Using the sub-answers below, craft a comprehensive and informative reply.

Original Query: {query}

Sub-answers:
{subanswers}

Guidelines:
- Speak in first person, warm and thoughtful. Treat this as a real-time conversation.
- Synthesize information from ALL sub-answers that contain relevant facts
- Prioritize factual information from the sub-answers over claims of insufficient data
- If multiple sub-answers provide information, combine them into a coherent response
- Only indicate uncertainty if ALL sub-answers lack information
- {answer_guidance}
- Close with a brief offer to help with follow-up questions.
- If a custom writing style is provided, blend it subtly: "{writing_style}"

Chat Reply:"""

VALIDATION_PROMPT = """You are a quality control expert. Evaluate whether the following answer adequately addresses the query.

Query: {query}

Answer: {answer}

If the answer fully addresses the query, output ONLY "OK".
If the answer is incomplete or misses key aspects, provide brief instructions for improvement (1-2 sentences).

Evaluation:"""

STYLE_PROMPT = """You are a writing assistant. Rewrite the following answer to match the specified persona and style, while preserving all factual information.

Persona/Style: {writing_style}

Original Answer: {answer}

Guidelines:
- Preserve all facts exactly as stated
- Match the tone and style of the persona
- Do not embellish or add information
- Keep the same level of detail

Styled Answer:"""
