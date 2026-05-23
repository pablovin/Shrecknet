ARCHITECT_SCENE_SEGMENTATION_PROMPT = """You are an expert narrative analyst.

Segment the text into coherent narrative scenes.

A Scene is a continuous narrative situation with a beginning, escalation, and outcome.
A scene is closer to a continuous camera shot than to a topic cluster.

Keep interactions unified while the same characters continue the same conversation, conflict, plan, or strategic objective.

Create a new scene ONLY when there is a meaningful change in:
- time
- location
- dominant participating characters

Do NOT split scenes for:
- planning progression
- tactical refinement
- new proposals within the same discussion
- emotional escalation within the same interaction
- continuation of the same strategic objective
- ongoing dialogue between the same characters

Prefer fewer, larger, stronger scenes.
Avoid fragmentation.

Scene titles and descriptions must reflect the dominant dramatic situation of the ENTIRE scene, not individual conversational beats.

Titles must:
- be concrete and descriptive
- capture conflict, pressure, decisions, or strategic intent
- avoid vague labels like:
  - Strategic Discussion
  - Rising Tension
  - Observation and Preparation

Descriptions must:
- describe the beginning, escalation, and outcome of the scene
- preserve the core tension and meaningful state change
- use explicit entity names whenever known

NEVER use vague references like:
- the group
- the party
- the foreigners
- they

when named entities are available.

Weak:
"The foreigners discuss their next move."

Strong:
"Tamura, Evrain, Lynelle, and Everin discuss how to manipulate Hold’s expectations while maintaining leverage over Leodogr and the bishop."

Return ONLY valid RFC8259 JSON.

{{
  "scenes": [
    {{
      "scene_id": 0,
      "name": "...",
      "description": "...",
      "start_paragraph": 1,
      "end_paragraph": 4
    }}
  ]
}}

Constraints:
- Do NOT include text outside JSON.
- Do NOT invent entities not present in the text.
- Output must be strict RFC8259 JSON.
- Use double quotes for all keys and string values.
- Do not use trailing commas.
- Do not include markdown fences.
- Ensure start_paragraph and end_paragraph are integers (not strings).

Paragraphs:
{marked_paragraphs}
"""


ARCHITECT_SCENE_SEGMENTATION_JSON_REPAIR_PROMPT = """You are a strict JSON repair assistant.

Task:
Repair the following malformed JSON output so it becomes valid strict RFC8259 JSON.
Do not change semantic content unless required for JSON validity.

Required final schema:
{
  "scenes": [
    {
      "scene_id": 0,
      "name": "...",
      "description": "...",
      "start_paragraph": 1,
      "end_paragraph": 4
    }
  ]
}

Rules:
- Return ONLY JSON.
- Use double quotes.
- No trailing commas.
- scene_id/start_paragraph/end_paragraph must be integers.
- Do not add markdown or explanations.

Malformed JSON:
{malformed_json}
"""


# Step 2: Batched scene-level entity extraction and reconciliation
ARCHITECT_ENTITY_PROPOSAL_PROMPT = """You are reviewing final scene spans and deciding which entities should be added to or matched against the persistent world graph.

Use ONLY the provided ontology definitions.

Ontology Definitions:
{ontology_definitions}

Existing Entities:
{existing_entities}

Scenes payload:
{scenes_payload}

Task:
For each scene, return ONLY entities that clearly satisfy ALL of the following:
1. They are explicitly named or clearly referred to in the text.
2. They are uniquely identifiable as the same entity beyond this scene.
3. They are meaningful enough to the scene story to persist in the world model.

For each returned entity:
- Set "status" to "existing" only when it clearly matches one of the Existing Entities by alias/name and ontology.
- Similar names like: Lady Anastasia and Anastasia should be considered a clear match if the scene context supports it, even if the shorter alias is not an exact match in the Existing Entities list. In this case, set "matched_alias" to the exact alias string from Existing Entities that it matches.
- Typos like King Leodrgance and King Leodogrance should be considered a clear match if the scene context supports it. In this case, set "matched_alias" to the exact alias string from Existing Entities that it matches.
- Set "status" to "new" when no listed Existing Entity is a clear match.
- For existing matches, set "matched_alias" to the exact alias string from Existing Entities. Also set the "ontology" to the ontology associated with that exact "matched_alias" in Existing Entities, even if the scene text suggests a different ontology. This is to enforce graph-truth and avoid mixing ontologies for the same entity.
- If "status" is "existing", the "ontology" MUST be the ontology associated with that exact "matched_alias" in Existing Entities.
- Never mix an existing alias with a different ontology; if you are not sure, always trust the ontology provided on the Existing Entities list.
- Do not invent existing aliases.
- Do not output ids. Existing entity ids are not part of this task.

Important:
- Returning an empty list is correct if the scene does not contain clear persistent entities.
- Do NOT extract generic objects, generic places, temporary descriptions, symbolic references, or vague groups.
- Do NOT infer entities that are not directly supported by the text.
- Prefer the most complete, clean, human-readable name.
- If two names refer to the same entity, keep only one.
- Use only the provided ontology definitions for the ontology field.
- The ontology value MUST exactly match one of the provided ontology definition names.
- NEVER output placeholders like "Unknown", "Other", or inferred ontology types not present in the provided list.

Practical guardrails:
- If a scene clearly contains at least one named character, named location, named faction, or named organization, return at least one entity.
- Use an empty list only when no clearly named persistent candidate exists in the scene text.
- Prefer obvious named entities over aggressive filtering.

Good candidates:
- named characters
- named locations
- named factions or organizations
- unique, clearly identified items with persistent story identity

Bad candidates:
- "the sword", "the stone", "the road", "the cathedral", "john`s horse", "The servant", "the mask", "the city"
- one-off background elements
- broad symbolic references unless clearly established as a concrete ontology entity

Confidence:
- 0 to 1
- should be high only when the entity is clearly grounded and clearly worth persisting

Why:
- one short sentence grounded in the text

Before outputting each entity, ask:
"Would I want this stored as its own reusable node in the world graph?"
If no, exclude it.

Return ONLY valid JSON in this exact format:
{{
  "scenes": [
    {{
      "scene_ref": "scene ref from input",
      "entities": [
        {{
          "name": "Entity Name",
          "ontology": "Character",
          "status": "existing|new",
          "matched_alias": "Exact Existing Entity Alias or null",
          "confidence": 0.85,
          "why": "Clearly named and directly involved in the scene."
        }}
      ]
    }}
  ]
}}
"""


# Step 3: Milestone extraction from proposed scene
ARCHITECT_MILESTONE_BATCH_PROMPT = """You are the Architect Agent.

Extract graph-worthy milestones from narrative scenes.

Scenes payload:
{scenes_payload}

A milestone is a concrete and meaningful narrative beat within a scene that changes the active situation, tension, knowledge, relationships, goals, or strategic position of the involved entities, and remains important enough to matter independently when retrieved later.


Extract milestones involving:
- decisions
- revelations
- threats
- confrontations
- commitments
- discoveries
- emotional or relationship shifts
- strategic turns
- meaningful consequences

Do NOT extract:
- routine movement
- filler dialogue
- atmosphere
- generic conversation
- minor actions

Keep continuous interactions unified.
Do NOT fragment milestones for tactical refinement or conversational progression.

Each scene must contain:
- one "begin" milestone
- one "end" milestone

Most scenes should contain 2-6 milestones. Be strict!

Use ONLY entities provided for the scene.
Prefer explicit entity names over vague references.
Add entity names in the milestone description to preserve them for retrieval and graph memory purposes.

Milestone titles must be:
- short
- concrete
- conflict-driven
- tied to meaningful actions or pressure
- include entity names if they are mentioned

Avoid vague titles like:
- Strategic Discussion
- Rising Tension
- Planning the Attack

Weak:
"The group discusses their next move."

Strong:
"Tamura, Lynelle, Evrain and Cwenhild Submit Cautiously"

Return STRICT RFC8259 JSON:

{{
  "scenes": [
    {{
      "scene_ref": "scene ref from input",
      "milestones": [
        {{
          "title": "short descriptive title",
          "description": "max 2 concise sentences",
          "boundary_type": "begin|end|none",
          "mentions": ["entity alias from this scene"],
          "adjacent_to": ["optional nearby milestone title"],
          "related_to": [
            {{
              "entity": "entity alias from this scene",
              "relationship_label": "verb-like label",
              "relationship_description": "short one-phrase explanation"
            }}
          ]
        }}
      ]
    }}
  ]
}}
"""


ARCHITECT_PROPERTY_EXTRACTION_PROMPT = """You are extracting ontology data for an entity using only provided evidence.

Entity context:
- entity_alias: {entity_alias}
- entity_type_name: {entity_type_name}

Existing autogenerated summary:
{existing_autogenerated_text}

Existing properties:
{existing_properties}

Existing relationships:
{existing_relationships}

Auto-generatable properties catalog:
{properties_catalog}

Auto-generatable relationships catalog:
{relationships_catalog}

Allowed relationship targets (explicit name/id/type):
{related_entities}

Scenes context:
{scenes_context}

Relevant text chunks:
[TEXT_CHUNKS]
{combined_chunks}
[/TEXT_CHUNKS]

Rules:
- Do not invent facts.
- Return only property values that are NEW or CHANGED.
- Return only relationships that are NEW or CHANGED.
- Choose properties by property_name from the properties catalog.
- Choose relationships by relationship_name from the relationships catalog.
- relationship_target must be an entity ID from the allowed related_entities list.
- For each relationship, obey destination type constraints in the relationships catalog.
- updated_autogenerated_summary must be a full rewritten summary, not an append/merge.

Return STRICT JSON only:
{{
  "properties_update": [
    {{"property_name": "Property Name", "property_value": "..."}}
  ],
  "relationships_update": [
    {{
      "relationship_name": "Relationship Name",
      "relationship_target": "entity_instance_id"
    }}
  ],
  "updated_autogenerated_summary": "full rewritten summary"
}}
"""


ARCHITECT_PROPERTY_UPDATE_PROMPT = ARCHITECT_PROPERTY_EXTRACTION_PROMPT
