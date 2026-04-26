ARCHITECT_SCENE_CENTRIC_CHUNKING_PROMPT = """You are an expert narrative analyst. Your task is to segment a text into coherent narrative scenes.

----------------
Input

You will receive a text divided into numbered paragraphs. Each paragraph is prefixed with an index in the format:

[P1] ...
[P2] ...
[P3] ...
...

Task

Segment the text into a sequence of scenes.

A scene is defined as:
- a contiguous span of paragraphs
- with continuity in time, place, and interaction focus
- centered around a specific situation, interaction, or narrative purpose
- it may contain multiple dialogue exchanges, actions, escalations, or immediate consequences

A new scene should begin only when there is a meaningful shift in:
- location
- time
- dominant participants
- goal or conflict
- narrative purpose

Important Rules

- Every paragraph must belong to exactly one scene.
- Scenes must be non-overlapping and must cover the entire text.
- Scenes must be returned in strict chronological order.
- Do NOT skip any part of the text.
- Do NOT merge unrelated interactions into one scene.
- Do NOT split scenes too finely.
- Prefer fewer, stronger, meaningful narrative units.
- Create a MAXIMUM of 6 scenes. If the text is very long, you may return fewer than 6 scenes, but never more than 6.

Anti-fragmentation Rule (very important)

Do NOT split a scene if the same interaction, conflict, or event is still unfolding continuously.

Keep setup, escalation, and immediate consequence in the SAME scene when:
- they happen in the same place
- they happen without a meaningful time break
- they involve mostly the same participants
- they serve the same narrative purpose

Examples of things that should usually stay in one scene:
- a quarrel that escalates into a fall or injury
- an omen followed immediately by a revelation
- a confrontation that directly turns into a duel declaration
- a battle and its immediate tactical turning point

For each scene, output:
- "scene_id": sequential integer starting from 0
- "name": short, descriptive label (max 6 words)
- "description": 1–2 sentence summary of what happens in the scene
- "start_paragraph": index of first paragraph in the scene
- "end_paragraph": index of last paragraph in the scene (inclusive)

Output Format

Return ONLY valid JSON in the following format:

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

Constraints

- Do NOT include any text outside JSON.
- Do NOT repeat the original text.
- Do NOT invent characters not present in the text.
- Scene names must be concise and concrete.
- Descriptions must be grounded in the text.

Final check before answering

Make sure:
- no adjacent scenes could be naturally merged without losing clarity
- no continuous interaction has been artificially split
- no scene is just a tiny fragment of a larger ongoing event

Paragraphs:
{marked_paragraphs}
"""

# Step 1b: Scene refinement and adjacent-scene unification
ARCHITECT_SCENE_UNIFIER_PROMPT = """You are refining a scene segmentation for a narrative text.

You will receive a list of already extracted scenes. Your task is to merge adjacent scenes when they are actually part of the same continuous narrative unit.

A merge is appropriate when:
- the scenes occur in the same location or no meaningful location change is present
- the same interaction, conflict, or event is still unfolding
- the second scene is a direct continuation, escalation, or immediate consequence of the first
- there is no meaningful time break
- the narrative purpose remains the same

Do NOT merge scenes if:
- a new major event begins
- a clear location shift occurs
- a different conflict becomes central
- a different participant group becomes dominant
- the narrative purpose clearly changes

Guidelines
- Prefer fewer, stronger scenes.
- Preserve chronological order.
- Preserve full paragraph coverage.
- Do NOT lose any content.
- Merge only adjacent scenes.

For each final scene, output:
- "scene_id": sequential integer starting from 0
- "name": short, descriptive label (max 6 words)
- "description": 1-2 sentence summary
- "start_paragraph": first paragraph index
- "end_paragraph": last paragraph index

Return ONLY valid JSON in the following format:

{{
  "scenes": [
    {{
      "scene_id": 0,
      "name": "...",
      "description": "...",
      "start_paragraph": 1,
      "end_paragraph": 10
    }}
  ]
}}

Final check before answering
Make sure:
- no adjacent scenes still look mergeable
- no final scene became too broad or incoherent

Scenes to refine:
{scene_list}
"""

# Step 2: Scene-level entity extraction
ARCHITECT_ENTITY_PROPOSAL_PROMPT = """You are reviewing a single scene span and deciding which entities, if any, should be added to the persistent world graph.

Use ONLY the provided ontology definitions.

Ontology Definitions:
{ontology_definitions}

Scene Information:
- Scene Name: {scene_name}
- Scene Description: {scene_description}

Scene Text:
\"\"\"{scene_text}\"\"\"

Task:
Return ONLY entities that clearly satisfy ALL of the following:
1. They are explicitly named or clearly referred to in the text.
2. They are uniquely identifiable as the same entity beyond this scene.
3. They are meaningful enough to persist in the world model.

Important:
- Returning an empty list is correct if the scene does not contain clear persistent entities.
- Do NOT extract generic objects, generic places, temporary descriptions, symbolic references, or vague groups.
- Do NOT infer entities that are not directly supported by the text.
- Prefer the most complete, clean, human-readable name.
- If two names refer to the same entity, keep only one.
- Use only the provided ontology definitions for the ontology field.
- The ontology value MUST exactly match one of the provided ontology definition names.
- NEVER output placeholders like "Unknown", "Other", or inferred ontology types not present in the provided list.

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
  "entities": [
    {{
      "name": "Entity Name",
      "ontology": "Character",
      "confidence": 0.85,
      "why": "Clearly named and directly involved in the scene."
    }}
  ]
}}
"""


# Step 3: Reconciliation with existing graph nodes
ARCHITECT_ENTITY_RECONCILATION_PROMPT = """You are reconciling extracted entities with an existing knowledge graph.

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


# Step 4: Milestone extraction from proposed scene
ARECHITECT_MILESTONE_PROPOSAL_PROMPT = """You are the Architect Agent.

Your task is to extract only graph-worthy milestones for a single scene.

Scene context:
- scene_ref: {scene_ref}
- scene_name: {scene_name}
- scene_description: {scene_description}

Entities present in this scene (you must only use these for milestone relations):
{scene_entities}

Scene text:
\"\"\"{scene_text}\"\"\"

Milestone definition:
A milestone is a concrete, important, graph-worthy action, decision, confrontation, transition, or state change that matters to the narrative beyond a single sentence.

A milestone is NOT:
- mere presence of a character
- general atmosphere or description
- passive introduction unless it clearly changes the narrative state
- generic movement or observation unless it has clear story importance
- low-impact narration that would not matter later

Core rule:
Always return at least 2 milestones per scene.
If the scene has fewer than 2 strongly meaningful beats, still return:
- one "begin" opening beat grounded in the scene
- one "end" closing beat grounded in the scene
Never return an empty milestone list.

Output size rules:
- A valid scene must have at least 2 milestones.
- For normal scenes, return exactly 2-4 milestones:
  - one with boundary_type "begin"
  - one with boundary_type "end"
- Only if the scene is clearly event-dense or contains multiple important turning points, you may return 5 to 6 milestones total.
- Never return more than 6 milestones.

Hard rules:
- Each milestone must be observable, specific, and written in present tense.
- title must be a short descriptive title (max 6 words) and never a generic placeholder.
- Never use titles like "Milestone 1", "Milestone 2", or similar numbering-only labels.
- Each milestone must describe something that actually happens or changes in the scene.
- The "begin" milestone should mark the meaningful opening state/action of the scene.
- The "end" milestone should mark the meaningful closing state/action of the scene.
- Additional milestones with boundary_type "none" are allowed only if they are clearly important.
- Do NOT create filler milestones just to increase the count.
- Do NOT create milestones for description alone.
- If only 1 clearly meaningful beat exists, pair it with a grounded opening or closing beat so output still has begin/end.
- Do NOT create relationships to entities not listed in scene_entities.
- relationship_label must be a single reusable word.
- relationship_description must be one short phrase.
- mentions must include only entities actually involved in that milestone.

Quality filter:
Before including a milestone, ask:
"Would this milestone help retrieve, explain, or connect important events in the world graph later?"
If NO, exclude it.

Return STRICT JSON only in this format:
{{
  "milestones": [
    {{
      "title": "short descriptive title (max 6 words)",
      "description": "single concrete action or state change in present tense",
      "boundary_type": "begin|end|none",
      "adjacent_to": ["optional nearby milestone title"],
      "related_to": [
        {{
          "entity": "entity alias from scene_entities",
          "relationship_label": "max two words with hyphen label for the relationship, usually a verb",
          "relationship_description": "short one-phrase explanation"
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
