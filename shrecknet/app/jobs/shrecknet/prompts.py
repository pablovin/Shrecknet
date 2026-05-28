REPAIR_INVALID_JSON_PROMPT = """You are a support agent executing the job: Repair Invalid Json.

Task:
Repair the following malformed JSON so it becomes valid strict RFC8259 JSON.
Do not change semantic content unless required for JSON validity.

Rules:
- Return ONLY JSON.
- Use double quotes.
- No trailing commas.
- Do not add markdown or explanations.

{schema_hint_block}
Malformed JSON:
{malformed_json}
"""
