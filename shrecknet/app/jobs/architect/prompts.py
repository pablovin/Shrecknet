"""Prompt templates for the Architect pipeline."""

# Step 1: Chunk-level entity extraction (NEW PIPELINE)
ARCHITECT_CHUNK_EXTRACTION_PROMPT = """You are extracting entities from a single text chunk. Use ONLY the provided ontology definitions to choose the entity type.

Ontology Definitions:
{ontology_definitions}

Text Chunk:
\"\"\"{chunk_text}\"\"\"

Rules:
- Do NOT create variants like "Jessie (old)" and "Jessie". Keep only the most complete, human-readable name.
- If two names in this chunk refer to the same entity (e.g. "Jessie" and "Jessie Williams"), keep only one, prefer the more complete one.
- Output a SLIM JSON array, no explanations outside JSON.
- Each item must have: name, ontology, confidence, why.
- Confidence is a float 0-1 indicating importance to the story.
- If the name clearly matches an ontology entry (e.g. a deity), use that ontology.
- Only include entities that are important to the story in this chunk.
- Do NOT include parenthetical clarifications in names (e.g., use "Mithras" not "Mithras (god)").

Return JSON in this exact format:
{{
  "entities": [
    {{
      "name": "Entity Name",
      "ontology": "Character",
      "confidence": 0.85,
      "why": "Brief 1-line justification"
    }}
  ]
}}
"""

# Step 3: Reconciliation with existing entities (NEW PIPELINE)
ARCHITECT_RECONCILIATION_PROMPT = """You are reconciling extracted entities with an existing knowledge graph.

Ontology Definitions:
{ontology_definitions}

Proposed Entities (extracted from text):
{proposed_entities}

Existing Entities (from knowledge graph):
{existing_entities}

Your task:
- For each proposed entity, decide if it is the same as an existing entity.
- Prefer matches to existing entities even if the name is slightly different.
- If a proposed entity is "Jessie" and the existing entity has alias "Jessie Williams", treat them as the SAME entity.
- If a proposed entity is "Mithras (god)" and there is an existing "Mithras", treat them as the SAME entity. Ignore parenthesis.
- The ontology field may be updated if the proposed entity has a more accurate ontology type than the existing one.

Output ONLY JSON with two arrays: existing and new.
- In existing, include proposed_name, matched_node_id, and ontology (updated if needed).
- In new, include name and ontology.

Return JSON in this exact format:
{{
  "existing": [
    {{
      "proposed_name": "Jessie Williams",
      "matched_node_id": "char_001",
      "ontology": "Character"
    }}
  ],
  "new": [
    {{
      "name": "Baron Jackie",
      "ontology": "Character"
    }}
  ]
}}
"""


ARCHITECT_SCENE_MILESTONE_PROPOSAL_PROMPT = """You are the Architect Agent.
Create a scene-centric proposal for the provided narrative text.

Scene definition:
- A continuous segment of time where setting and situation remain stable.
- Starts when a situation begins and ends when it clearly changes (location, participants, or objective).

Milestone definition:
- A single, concrete action or state change at a specific moment in a scene.

Hard constraints:
Scenes:
- No time jumps inside a scene.
- Do not mix separate moments in one scene.
- Each scene must feel like one episode of action.

Milestones:
- Must be short and direct.
- Must describe only one action/event.
- Must be in present tense.
- Must be observable (no vague interpretation).

Rules:
- Each scene MUST include at least one begin milestone and one end milestone.
- Prefer more milestones rather than fewer.
- Split scenes when location changes, time jumps occur, participants change significantly, or the situation/goal changes.
- Bad milestone: "They argue intensely"
- Good milestones:
  - "John accuses Mary of betrayal"
  - "Mary denies the accusation"
  - "John leaves the tavern"

Author context:
- created_by_type = "agent"
- created_by_author = "{author_id}"
- derived_from.entity_instance_id = "{source_entity_instance_id}"

Known entity aliases (for mentions/RELATES_TO candidates):
{known_aliases}

Narrative text chunks:
{chunk_dump}

Return STRICT JSON only in this format:
{{
  "scenes": [
    {{
      "name": "short scene name",
      "description": "what is happening in this scene",
      "mentions": ["entity alias"],
      "milestones": [
        {{
          "label": "short verb-like label",
          "description": "single concrete action in present tense",
          "boundary_type": "begin|end|none",
          "mentions": ["entity alias"]
        }}
      ]
    }}
  ]
}}
"""


ARCHITECT_RELATES_TO_PROPOSAL_PROMPT = """You are the Architect Agent.
Resolve RELATES_TO candidates for scene/milestone proposals.

Source proposals:
{source_nodes}

Candidate entity aliases:
{candidate_aliases}

Rules:
- Only output links that are explicitly mentioned or strongly implied.
- Do not output ambiguous links.
- If a source has no reliable link, output no relation for it.

Return STRICT JSON only:
{{
  "relationships": [
    {{
      "source_ref": "scene_or_milestone_ref",
      "source_kind": "scene|milestone",
      "target_alias": "known alias",
      "confidence": 0.0,
      "evidence": "short evidence text"
    }}
  ]
}}
"""

# Original extraction prompt (kept for backward compatibility)
ARCHITECT_EXTRACTION_PROMPT = """You are the Architect Agent. Your task is to analyse a story excerpt
and suggest ontology instance updates using the provided schema.

YOUR PRIMARY TASK: Identify which entities in the text should UPDATE existing instances vs CREATE new instances.

Ontology Entities (id, name, description):
{entity_catalog}

Candidate Existing Instances (instance_id, alias, summary):
{existing_instances}

Story Chunk:
\"\"\"{chunk_text}\"\"\"

Return STRICT JSON with the following structure:
{{
  "new_instances": [
    {{
      "alias": "string",
      "entity_definition_id": 123,
      "confidence": 0.0,
      "justification": "why this matters",
      "metadata": {{
        "supporting_sentences": ["optional text snippets"]
      }}
    }}
  ],
  "existing_instances": [
    {{
      "entity_instance_id": "string",      
      "entity_definition_id": 123,
      "confidence": 0.0,
      "justification": "why this is relevant and what NEW information is available",
      "metadata": {{
        "alias": "entity alias from the candidate list",
        "supporting_sentences": ["optional text snippets"]
      }}
    }}
  ]
}}

CRITICAL DECISION LOGIC:
1. USE "existing_instances" when:
   - An entity in the text matches (exactly or partially) an entity alias in the Candidate Existing Instances list
   - The entity name is a variation, abbreviation, or full form of an existing entity
   - Examples: "Wentworth" matches "Prof. Wentworth", "Jack" matches "Jack Radford", "the Prince" matches "Prince Marcus"
   - The text provides NEW information about an entity that already exists
   - The entity is referenced more than again with additional context or details
   - The entity has an importance to the story told on the chunk

2. USE "new_instances" ONLY when:
   - The entity is clearly distinct from all existing instances
   - No existing instance could reasonably refer to this entity
   - It's a completely new character, location, organization, etc.
   - This entity has a measurable importance to the story told on the chunk

Rules:
- ALWAYS prefer updating existing instances over creating new ones when there's any reasonable match
- If there are no suggestions, return empty arrays
- confidence must be between 0 and 1, based on the importance of this entity to the chunk
- entity_definition_id must be an integer from the entity catalog
- entity_instance_id must come from the existing instances list
- Never add any definition of the entity example: Mithras (God). Only Mithras is enough.
- The response MUST be valid JSON with double quotes for strings (NOT backslash-quote)
- When including text in arrays, use proper JSON string syntax: ["text"] NOT [\\"text\\"]
- For existing_instances, ALWAYS include the "alias" field in metadata with the entity's alias from the candidate list
- For existing_instances, the justification should explain what NEW information is present in the text
"""

ARCHITECT_PROPERTY_EXTRACTION_PROMPT = """You are the Architect Agent. Your task is to extract properties and relationships for an entity from story text.

Entity Information:
- Entity Definition ID: {entity_definition_id}
- Entity Alias: {entity_alias}
- Entity Type: {entity_type_name}

Available Auto-generatable Properties (extract these if present in text):
{properties_catalog}

Available Auto-generatable Relationships (extract these if present in text):
{relationships_catalog}

Story Text Chunks:
\"\"\"
{combined_chunks}
\"\"\"

Full Original Text Context:
\"\"\"
{original_text}
\"\"\"

Return STRICT JSON with the following structure:
{{
  "properties": [
    {{
      "definition_id": 123,
      "value": "extracted value from text"
    }}
  ],
  "relationships": [
    {{
      "definition_id": 456,
      "target_alias": "name of related entity",
      "justification": "why this relationship exists based on the text"
    }}
  ],
  "autogenerated_summary": "A concise 2-3 sentence summary of this entity based on all the text provided. This should be complete and informative, suitable as a short description."
}}

Rules:
- ONLY extract properties and relationships that are explicitly mentioned or strongly implied in the text.
- Do NOT invent information not present in the text.
- For relationships, the target_alias must refer to an entity name/alias that appears in the text or is otherwise known.
- If no properties or relationships are found, return empty arrays.
- The autogenerated_summary should synthesize information from all text chunks into a coherent, concise description.
- For numerical properties, extract the number as a string or number type.
- For text properties, extract the relevant text snippet.
- The response MUST be valid JSON with double quotes.
"""

ARCHITECT_PROPERTY_UPDATE_PROMPT = """You are the Architect Agent. Your task is to update properties and relationships for an existing entity with new information from story text.

Entity Information:
- Entity Instance ID: {entity_instance_id}
- Entity Alias: {entity_alias}
- Entity Type: {entity_type_name}

Existing Entity Text:
\"\"\"
{existing_text}
\"\"\"

Existing Autogenerated Summary:
\"\"\"
{existing_autogenerated_text}
\"\"\"

Existing Properties:
{existing_properties}

Existing Relationships:
{existing_relationships}

Available Auto-generatable Properties (add new ones if present in new text):
{properties_catalog}

Available Auto-generatable Relationships (add new ones if present in new text):
{relationships_catalog}

New Story Text Chunks:
\"\"\"
{combined_chunks}
\"\"\"

Full Original Text Context:
\"\"\"
{original_text}
\"\"\"

Return STRICT JSON with the following structure:
{{
  "new_properties": [
    {{
      "definition_id": 123,
      "value": "extracted value from text"
    }}
  ],
  "new_relationships": [
    {{
      "definition_id": 456,
      "target_alias": "name of related entity",
      "justification": "why this relationship exists based on the text"
    }}
  ],
  "updated_autogenerated_summary": "An updated concise 2-3 sentence summary incorporating both the existing information and the new information from the text. This should be complete and informative, suitable as a short description."
}}

Rules:
- ONLY include properties and relationships in new_properties and new_relationships that DO NOT already exist.
- Do NOT duplicate existing properties or relationships.
- ONLY extract information that is explicitly mentioned or strongly implied in the new text.
- Do NOT invent information not present in the text.
- For relationships, the target_alias must refer to an entity name/alias that appears in the text or is otherwise known.
- If no new properties or relationships are found, return empty arrays.
- The updated_autogenerated_summary should integrate existing information with new information into a coherent, concise description.
- The response MUST be valid JSON with double quotes.
"""


ARCHITECT_TIMELINE_FROM_NODE_PROMPT = """You are the Architect Agent.
Generate timeline events for one entity based only on the provided text chunk.

Entity:
- alias: {entity_alias}
- entity_instance_id: {entity_instance_id}

Available related aliases (existing entities in this page; use these exact names):
{available_aliases}

Text Chunk:
\"\"\"{chunk_text}\"\"\"

Return ONLY valid JSON array (no markdown, no commentary):
[
  {{
    "title": "short event title",
    "description": "1-3 sentences with concrete facts",
    "source_alias": "{entity_alias}",
    "related_aliases": ["optional other alias"],
    "order": 1
  }}
]

Rules:
- Use only facts present in the chunk.
- Keep events chronological for this chunk (`order` ascending).
- Avoid duplicates or near-duplicates.
- Prefer specific, concrete action titles (who did what, where) over abstract labels.
- Bad title example: "Angelique's determination"
- Good title example: "Angelique enters the Jewish quarter to search for allies"
- Always try to include `related_aliases` using exact names from the available aliases list.
- Never include the source entity itself in `related_aliases`.
- If no event is present, return []
- Return at most {max_events} events for this chunk.
"""


ARCHITECT_TIMELINE_SELECTION_PROMPT = """You are selecting the final timeline events for one entity.

Entity:
- alias: {entity_alias}
- entity_instance_id: {entity_instance_id}

Available related aliases (existing entities in this page; use these exact names):
{available_aliases}

Goal:
- Select the {max_events} most meaningful events from the candidate list.
- Ensure temporal coverage across the whole text (include early and late events when relevant).
- Keep chronology in the output.
- Rewrite titles to be concrete and descriptive actions.

Candidate events JSON:
{candidate_events_json}

Return ONLY valid JSON array (no markdown):
[
  {{
    "title": "concrete action title",
    "description": "clear and specific factual description",
    "source_alias": "{entity_alias}",
    "related_aliases": ["optional alias"],
    "order": 1,
    "candidate_index": 0
  }}
]

Rules:
- Do not invent events not present in candidates.
- Prefer events with high story impact or clear change.
- Avoid generic titles ("determination", "conflict", "turning point").
- Use `related_aliases` with exact names from the available aliases list whenever relevant.
- Never include the source entity itself in `related_aliases`.
- Keep output to at most {max_events} events.
- Output must be chronologically ordered.
"""
