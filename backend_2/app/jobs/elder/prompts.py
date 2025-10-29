"""Prompt templates for Elder job pipeline."""

DECOMPOSE_PROMPT = """You are an expert query analyzer. Break down the following query into 1-5 focused sub-queries that will help answer the original question comprehensively.

The query is related to ontologies with IDs: {ontology_ids}

Original Query: {query}

Guidelines:
- Create specific, focused sub-queries
- Each sub-query should target a distinct aspect
- If the query is already focused, you may return just 1 sub-query
- Maximum 5 sub-queries
- Return ONLY the sub-queries, one per line, without numbering or explanations

Sub-queries:"""

SUBANSWER_PROMPT = """You are a helpful assistant. Answer the following question using ONLY the provided context. If the context is insufficient, respond with "Insufficient context."

Question: {subquery}

Context:
{context}

Guidelines:
- Only use information from the provided context
- Be concise and factual
- If the context doesn't contain relevant information, say "Insufficient context."
- Do not make assumptions or add information not in the context

Answer:"""

SYNTHESIS_PROMPT = """You are a knowledgeable assistant synthesizing information. Create a comprehensive answer to the original query by combining the following sub-answers.

Original Query: {query}

Sub-answers:
{subanswers}

Guidelines:
- Synthesize a coherent, comprehensive response
- Acknowledge any uncertainties or gaps in information
- Be clear and well-structured
- Maintain factual accuracy from the sub-answers
- If multiple sub-answers say "Insufficient context," acknowledge the lack of information

Final Answer:"""

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
