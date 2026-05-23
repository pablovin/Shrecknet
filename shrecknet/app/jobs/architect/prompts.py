ARCHITECT_SCENE_SEGMENTATION_PROMPT = """You are an expert narrative analyst. Your roll is to segment a source text chunk into meaningful narrative scenes based on continuity in time, place, interaction focus, and narrative purpose.

Segment the text into coherent narrative scenes.

A Scene is a continuous narrative situation.
A scene is closer to a continuous camera shot than to a topic cluster.

A scene remains unified while the same core interaction, conflict, conversation, or strategic objective continues evolving.

Keep conversations, confrontations, planning, arguments, tactical refinement, and immediate consequences together while the interaction continues.

Create a new scene ONLY when there is a meaningful change in:
- time
- location
- dominant participating characters
- ongoing interaction or activity

Do NOT create new scenes for:
- planning progression
- tactical refinement
- new proposals within the same discussion
- escalation within the same interaction
- continuation of the same strategic objective
- emotional escalation within the same interaction
- new information revealed during the same continuous exchange

Prefer fewer, larger, stronger scenes.
Avoid fragmentation.

Before creating a scene title or description, identify the single dominant dramatic situation connecting the ENTIRE scene.

Scene descriptions must capture:
- the dominant dramatic situation
- the active tension or pressure
- important social, emotional, strategic, or political dynamics
- the meaningful state change produced by the scene

Descriptions should preserve the enduring narrative situation of the scene, not summarize individual conversational beats.

Do NOT write generic plot summaries.

Prefer explicit character and entity names over vague references.

Avoid vague references like:
- the group
- the party
- the foreigners
- they
- the item
- the city
- the place

when the involved entities are known.

Preserve important entity names for retrieval and graph memory purposes.

Scene titles must reflect the dominant dramatic situation of the ENTIRE scene.

Titles should feel like memorable narrative beats, not topic labels or chapter categories.

Avoid vague or thematic titles like:
- Observation and Preparation
- Strategic Discussion
- Rising Tension
- Planning the Attack
- The Conversation
- Calculated Defiance

Prefer titles tied to:
- named characters
- concrete conflict
- political pressure
- strategic intent
- revelations
- meaningful decisions

Weak title:
"Observation and Preparation"

Strong title:
"Tamura Organizes Surveillance"

Weak description:
"The group discusses their next move."

Strong description:
"Tamura, Evrain, Lynelle, and Everin discuss surveillance, allies, and timing while preparing for a prolonged political struggle in Salt."

Weak description:
"King Leodogr issues warnings to the foreigners."

Strong description:
"King Leodogr restrains his anger toward Tamura, Evrain, Lynelle, and Everin while imposing strict conditions on their stay, revealing Arthur’s political protection over them."

Return ONLY valid JSON:

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


ARCHITECT_SCENE_DEDUP_PROMPT = """You are merging scene segmentation metadata for one source entity.

Input scenes are already ordered and include metadata + source paragraph text. Your task is to merge only clear duplicates or obvious over-splits.

Merge scenes only when they clearly describe the same continuous time/place/goal/participant situation. Keep unrelated scenes separate.

Conservative merge policy:
- Prefer under-merging rather than over-merging.
- Do NOT merge scenes that are only topically similar.
- Do NOT merge solely because two scenes are adjacent.
- If uncertain, keep scenes separate.

Return ONLY valid JSON in this exact format:

{{
  "merged_scenes": [
    {{
      "scene_refs": ["input-scene-ref-1", "input-scene-ref-2"],
      "name": "short merged title",
      "description": "1-3 concise sentences describing what happens. Do not perform entity extraction or add entity relationship details.",
      "source_paragraphs": [12, 13, 14, 15]
    }}
  ]
}}

Rules:
- Every input scene_ref must appear exactly once in merged_scenes.
- Preserve chronological order.
- Do not invent new scene_refs.
- name/description must be rewritten for the merged scene (do not just copy one original scene unchanged when multiple scenes are merged).
- source_paragraphs must be the merged unique paragraph indexes across all scene_refs in that merged scene, sorted ascending.
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
- Similar names like: Lady Anastasia and Anastasia should be considered a clear match if the scene context supports it, even if the shorter alias is not an exact match in the Existing Entities list. In this case, set "matched_alias" to the exact alias string from Existing Entities that it matches.
- Typos like King Leodrgance and King Leodogrance should be considered a clear match if the scene context supports it. In this case, set "matched_alias" to the exact alias string from Existing Entities that it matches.
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

Your task is to extract graph-worthy milestones for a batch of finalized narrative scenes.

Scenes payload:
{scenes_payload}

----------------
Core Concept

A milestone is a persistent narrative state transition.

A milestone captures a meaningful change that affects:
- goals
- knowledge
- danger
- relationships
- trust
- emotional state
- authority
- conflict
- commitments
- future narrative direction

A good milestone creates narrative pressure or meaningful consequences.

A milestone should still matter if retrieved independently later.

----------------
Narrative Pressure

Prefer milestones involving:
- revelations
- confrontations
- decisions
- commitments
- refusals
- discoveries
- betrayals
- victories
- failures
- threats
- escalations
- emotional shifts
- relationship changes
- changes in control or authority
- transitions in objectives or danger
- irreversible or difficult-to-reverse actions

Each milestone should imply a BEFORE and AFTER state.

If nothing meaningfully changed after the event,
it is probably not graph-worthy.

----------------
What NOT To Extract

A milestone is NOT:
- mere presence of a character
- passive atmosphere
- generic movement
- entering or leaving locations
- idle dialogue
- low-impact narration
- generic observation
- scene setup with no consequence
- travel unless something important changes during it
- filler interaction

Avoid milestones that only describe:
- characters arriving somewhere
- characters looking around
- characters speaking without consequence
- routine actions
- environmental description

Unless the action creates a meaningful narrative change.

----------------
Compression Rules

Do NOT extract every action separately.

Combine tightly related exchanges and actions into a single milestone
when they form one coherent narrative beat.

Focus on turning points and meaningful transitions.

----------------
Scene Boundary Rules

Every scene must include:
- one "begin" milestone
- one "end" milestone

The "begin" milestone should establish:
- the initiating pressure
- the core goal
- the interruption
- the conflict
- the immediate narrative tension

The "end" milestone should capture:
- the resulting state change
- the decision reached
- the unresolved tension
- the consequence
- the transition caused by the scene

Boundary milestones must still represent meaningful state changes.
Do NOT create filler milestones just to satisfy boundaries.

----------------
Milestone Count Rules

- Most scenes should contain 2-4 milestones.
- Dense or highly dynamic scenes may contain 5-6 milestones.
- Never return more than 6 milestones per scene.
- Never extract filler milestones to increase count.

Quality is more important than quantity.

----------------
Entity Rules

Each scene payload contains allowed entities.

Rules:
- Use ONLY entities allowed for that scene.
- mentions must contain only entities explicitly involved in the milestone.
- related_to must contain only entities directly participating in the milestone.
- Do not infer entities not supported by the scene text.
- Prefer explicit entity mentions in descriptions when available.

relationship_label:
- must be short
- reusable
- verb-like
- graph-friendly

Good examples:
- reveals
- threatens
- confronts
- protects
- rejects
- discovers
- attacks
- assists
- suspects
- commands
- deceives
- follows
- escapes

Avoid vague labels like:
- interacts_with
- is_with
- talks_to
- exists_near

relationship_description:
- one short phrase
- concrete
- contextual

----------------
Writing Rules

title:
- short descriptive title
- max 6 words
- concrete and specific

description:
- concise
- concrete
- present tense
- max 2 sentences
- describe what meaningfully changes
- identify key involved entities when appropriate

Descriptions should focus on:
- what changed
- what was decided
- what was revealed
- what escalated
- what consequence emerged

----------------
Priority Rules

Prioritize:
1. revelations and decisions
2. confrontations and commitments
3. emotional or relational changes
4. consequential actions
5. environmental transitions only if consequential

----------------
Examples

Weak milestone:
- "The group enters the castle."

Strong milestone:
- "The castle guards deny the party entry and demand proof of royal authority."

Weak milestone:
- "Maria talks to John."

Strong milestone:
- "Maria admits she betrayed the resistance to protect her brother."

Weak milestone:
- "The group leaves town."

Strong milestone:
- "The party abandons the town after realizing the plague has already spread beyond containment."

----------------
Graph Utility

Milestones should be independently useful for:
- retrieval
- temporal reasoning
- memory reconstruction
- relationship tracking
- narrative continuation

Prefer milestones that would remain meaningful if retrieved alone.

----------------
Return Format

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
