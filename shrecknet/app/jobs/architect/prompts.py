ARCHITECT_SCENE_SEGMENTATION_PROMPT = """You are an expert narrative analyst. Segment this full paragraph chunk into final narrative scenes.

Input

You will receive one source chunk divided into numbered paragraphs:

[P1] ...
[P2] ...
[P3] ...
...

Task

Return the final scene segmentation for THIS ENTIRE CHUNK in one pass. Do not create local candidates. Do not extract milestones.

A scene is a contiguous span of paragraphs with continuity in time, place, interaction focus, and narrative purpose.

Create a new scene only when there is a clear, meaningful shift in one or more of:
- time
- location
- dominant participants
- goal or conflict
- Narrative purpose alone is NOT sufficient to split a scene unless accompanied by a meaningful shift in time, location, or interaction focus.


Anti-fragmentation rules:
- Prefer fewer, stronger scenes.
- Do NOT split a scene while the same interaction, conflict, or event is still unfolding.
- Keep setup, escalation, and immediate consequence together when they happen without a meaningful break.
- Do NOT create scenes for mood, description, or minor action alone.
- Continuous conversations or confrontations should usually remain within the same scene unless there is a clear interruption or transition.
- Avoid one-paragraph micro-scenes unless there is an explicit hard transition (clear time jump, location jump, or cast reset).
- Prefer scenes with at least 2-3 paragraphs when possible.

Scene count guidance (soft targets, not rigid):
- 1-15 paragraphs: usually 1-3 scenes
- 16-30 paragraphs: usually 2-5 scenes
- 31+ paragraphs: usually 3-6 scenes
- Do not exceed 8 scenes for a chunk unless the text contains many explicit hard transitions.

Coverage rules:
- Scenes must be chronological.
- Scenes must cover the whole chunk without gaps.
- Scenes must not overlap.
- Each scene must use paragraph indexes from this chunk.
- Do not infer unseen transitions between paragraphs unless explicitly supported by the text.

For each scene, output:
- "scene_id": sequential integer starting from 0
- "name": short descriptive title, max 6 words
- "description": 1-3 concise sentences describing what happens. Do not perform entity extraction or add entity relationship details.
- "start_paragraph": first paragraph index, inclusive
- "end_paragraph": last paragraph index, inclusive

Return ONLY valid JSON in this exact format:

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
- Do NOT include milestones.
- Do NOT include entity lists.
- Do NOT include mentions or related_to.
- Do NOT include text outside JSON.
- Do NOT repeat the source text.
- Do NOT invent entities not present in the text.

Paragraphs:
{marked_paragraphs}
"""


ARCHITECT_SCENE_DEDUP_PROMPT = """You are merging scene segmentation metadata for one source entity.

Input scenes are already ordered and include only metadata, not source prose. Your task is to merge only clear duplicates or obvious over-splits.

Merge scenes only when they clearly describe the same continuous time/place/goal/participant situation. Keep unrelated scenes separate.

Conservative merge policy:
- Prefer under-merging rather than over-merging.
- Do NOT merge scenes that are only topically similar.
- Do NOT merge solely because two scenes are adjacent.
- Merge adjacent scenes only if they are clearly the same ongoing event with no meaningful transition.
- If uncertain, keep scenes separate.

Return ONLY valid JSON in this exact format:

{{
  "merged_scenes": [
    {{
      "scene_refs": ["input-scene-ref-1", "input-scene-ref-2"],
      "name": "short merged title",
      "description": "1-3 concise sentences describing what happens. Do not perform entity extraction or add entity relationship details."
    }}
  ]
}}

Rules:
- Every input scene_ref must appear exactly once in merged_scenes.
- Preserve chronological order.
- Do not invent new scene_refs.
- Do not include source text.
- Keep scene boundaries unless there is strong evidence they represent the same event.

Scene metadata:
{scene_metadata}
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
3. They are meaningful enough to persist in the world model.

For each returned entity:
- Set "status" to "existing" only when it clearly matches one of the Existing Entities by alias/name and ontology.
- Set "status" to "new" when no listed Existing Entity is a clear match.
- For existing matches, set "matched_alias" to the exact alias string from Existing Entities.
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
- "the sword", "the stone", "the road", "the cathedral", "john`s horse"
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

Your task is to extract graph-worthy milestones for a batch of final scenes.

Scenes payload:
{scenes_payload}

Milestone definition:
A milestone is a concrete, important, graph-worthy action, decision, confrontation, transition, or state change that matters to the narrative beyond a single sentence.

A milestone is NOT:
- mere presence of a character
- general atmosphere or description
- passive introduction unless it clearly changes the narrative state
- generic movement or observation unless it has clear story importance
- low-impact narration that would not matter later

Core rules:
- Return milestones grouped by scene_ref.
- Always return at least 2 milestones per scene.
- Include one "begin" opening beat and one "end" closing beat per scene.
- For normal scenes, return 2-4 milestones.
- Only clearly event-dense scenes may return 5-6 milestones.
- Never return more than 6 milestones per scene.

Entity rules:
- Each scene payload includes allowed entities for that scene.
- Use only those entities in mentions and related_to.
- Milestone descriptions should mention involved entities by name when supported by the scene text.
- related_to must include only entities actually involved in that milestone.
- relationship_label must be a reusable verb-like label.
- relationship_description must be one short phrase.

Writing rules:
- title must be a short descriptive title, max 6 words.
- description must be concise, concrete, present tense, and max 2 sentences.
- Each description must explain what happens or changes and identify key involved entities when available.
- Do NOT create filler milestones just to increase the count.

Return STRICT JSON only in this format:
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
