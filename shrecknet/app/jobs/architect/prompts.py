# Used by: Architect Analyze job (`architect.analyze_instance`), scene chunking phase.
# Callsite: `app/jobs/architect/scene_centric_chunking.py::segment_chunk_into_scenes`.
# Goal: Segment chunk-level paragraph-marked narrative text into coherent scenes with
# global paragraph boundaries (`start_paragraph`, `end_paragraph`) for downstream proposals.
ARCHITECT_SCENE_SEGMENTATION_PROMPT = """
You are an expert narrative analyst helping build a knowledge graph from long-form story text.

Your task is to segment the provided text into coherent narrative scenes.

The input text is divided into numbered paragraphs, using markers such as [P1], [P2], [P3], etc.

A scene is a continuous storytelling situation. It has a clear narrative focus, usually involving the same main characters, location, time period, and ongoing interaction or objective. 
A scene should feel like a continuous passage in a novel, or like one extended camera sequence: something begins, develops, and reaches a meaningful pause, result, or transition.

Scenes will later be used by other agents to extract entities, relationships and milestones. So each scene has to be very well detailed.

A scene should usually remain unified while:
- the same characters continue the same conversation
- the same conflict or plan is being developed
- the same action sequence continues
- the same emotional or strategic objective is unfolding
- the same location and time are maintained


ENTITY-GROUNDED WRITING RULE

Scene names and descriptions must be grounded in explicit textual entities.

A scene name MUST begin with:
1. a named character, if one appears in the scene span;
2. otherwise, a named place, faction, institution, object, or event from the scene span;
3. otherwise, the most specific role phrase explicitly used in the text.

Do not invent names.

Do not write abstract titles such as:
- "Tensions Among the Travelers"
- "Arrival at the Ruined City"
- "A Dangerous Encounter"
- "The Group Debates the Plan"

Instead, write titles such as:
- "Elaine and Astenius Clash During Evrain’s March"
- "Evrain Arrives at the Ruined Roman City"
- "Elaine Challenges Evrain After the Horse Bolts"

Descriptions must also use explicit names whenever they are available.

Forbidden generic references when a more specific name is available:
"the travelers", "the group", "the party", "the companions", "the foreigners", "the riders", "the narrator", "the man", "the woman", "the commander", "the squire".

If the text gives only a role and no proper name, use the exact role phrase from the text, such as "the Saxon commander", "the Irish noblewoman", or "the young squire".


CRITICAL OUTPUT RULE:
If a scene does not have start_paragraph and end_paragraph, the output is invalid.
DO NOT invent anything. Only use the information from the paragraphs.
Do not create scenes and narrative that is not present on the paragraphs.

"description": "3-6 sentences describing the scene, using explicit named entities instead of generic labels"
Return ONLY valid RFC8259 JSON.

{{
  "scenes": [
    {{
 "scene_id": 1,
       "name": "Elaine Startles Evrain's Horse and Asterius Rides After Him",
  "description": "Elaine, daughter of King Lot, confronts Evrain after the Pictish attack leaves the road march tense and unstable. Evrain mocks Elaine, escalating the conflict between them instead of restoring order. Elaine reacts in anger and startles Evrain's horse, causing the horse to bolt with Evrain. Asterius, the Roman knight, decides to pursue Evrain and intervene before the incident becomes fatal."
      "start_paragraph": 12,
      "end_paragraph": 98
    }}
  ]
}}

Constraints:
- Do NOT include text outside JSON.
- Use double quotes for all keys and string values.
- Do not use trailing commas.
- Do not use markdown fences.
- Ensure start_paragraph and end_paragraph are integers (not strings).

Paragraphs:
{marked_paragraphs}
"""


# Used by: Architect Analyze job (`architect.analyze_instance`) and Novelist scaffolding flow.
# Callsites:
# - `app/tasks/architect_analysis.py::_extract_scene_entities`
# - `app/jobs/novelist/novelist.py`
# Goal: Propose scene-level graph-worthy entities and classify each as `existing` or `new`
# against current ontology definitions and existing alias catalog.
ARCHITECT_ENTITY_PROPOSAL_PROMPT = """
You are an expert narrative analyst helping build a knowledge graph from long-form story text.
Your goal is to decide which entities from the given unstructured text scenes should be added to or update existing scenes inside the world graph.
For that, I will give you a list of ontology definitions. Use them to associate newly proposed entities to.
I also give you a list of existing entities in the graph, each strongly associated with an ontology.
So please decide if the entity you are extracting is new or already exists in the graph.
Then I will give you a list of scenes, each with text description and text excerpt.
Your task is to extract the list of entities (new or existing) from those scenes.
The entities you return must be clearly explicitly named on the text.
Some entities have variations on their names, or typos. So match them with the existing entities list if possible.
Only return entities that deserve to be persisted in the world graph.

Avoid generic entities such as: "The Boy", "The Sword", "The City" etc... Only return named entities.
Use ONLY the provided ontology definitions. Do not invent ontologies, and for all returned entities, match them with the provided ontology definitions.
If "status" is "existing", the "ontology" MUST be the ontology associated with that exact "matched_alias" in Existing Entities.

Try to match all named entities in the text, we should not miss anyone important.

Good candidates:
- John, Lucia, Bishop Leodogr
- London, The Catedral of Light, the Subplane of Devotion
- The Order of the Rose, the Rocket Team

Bad candidates:
- "the sword", "the stone", "the road", "the cathedral", "john`s horse", "The servant", "the mask", "the city"
- Entities that appear only once or do not carry any meaningful weight in the scene.


Before outputting each entity, ask:
"Would I want this stored as its own reusable node in the world graph?"
If no, exclude it.


Return ONLY valid RFC8259 JSON.
{{
  "scenes": [
    {{
      "scene_ref": "scene ref from input",
      "entities": [
        {{
          "name": "Entity Name",
          "ontology": "the name of the matched or proposed ontology",
          "status": "existing|new",
          "matched_alias": "Exact Existing Entity Alias or null",
          "confidence": 0 to 1 (should be high only when the entity is clearly grounded and clearly worth persisting), 
          "why": " one short sentence grounded in the text."
        }}
      ]
    }}
  ]
}}



Ontology Definitions:
{ontology_definitions}

Existing Entities:
{existing_entities}

Scenes payload:
{scenes_payload}




"""


# Used by: Architect Analyze job (`architect.analyze_instance`), milestone proposal phase.
# Callsite: `app/tasks/architect_analysis.py::_run_milestone_proposal_phase`.
# Goal: Extract 2-5 meaningful milestones per scene, with scene-bounded `related_to`
# entity links and boundary markers (`begin|end|none`) for proposal review.
ARCHITECT_MILESTONE_BATCH_PROMPT = """

You are an expert narrative analyst helping build a knowledge graph from long-form story text.
Your goal is to decide which milestones from the provided scenes should be added to or matched against the persistent world graph.

A milestone is a concrete narrative beat that meaningfully changes the situation, tension, goals, knowledge, relationships, or strategic position within a scene.
A collection of milestones should capture the core narrative of a scene.

Keep continuous interactions unified.
Do NOT fragment milestones for conversational progression, tactical refinement, or multiple proposals within the same ongoing interaction.
Milestones must involve: decisions, vulnerabilities, commitments, revelations, threats, confrontations, discoveries.

Every scene must contain 2-5 meaningful milestones.
Every scene must have at least 2 milestones. Always. Enforce that. If a scene has fewer than 2 milestones, merge them aggressively until you reach at least 2. 

Milestone titles must be descriptive of their part on the scene. Do not use The Narrator, nor anything like that.
Descriptions must be concise, maximum 6 sentences, and capture the core of the milestone. 

Do not use vague references like "narrator", "they", "the group", "the foreigners", "the party", etc. 

Weak:
"Title: The group discusses their next move."
"Description: The foreigners discuss their next move."

Strong:
"Title: Tamura, Lynelle and Cwenhild Commits to Restraint"
"Description: During a tense discussion, Tamura, Lynelle and Cwenhild agree to restrain from taking any aggressive action against Hold for now, 
in order to avoid provoking him and to maintain leverage over Leodogr and the bishop. This commitment shapes their strategy moving forward and creates 
internal tension as they struggle with their desire for revenge against Hold."


You are the Architect Agent.

Extract meaningful milestones from narrative scenes.



Return STRICT RFC8259 JSON.

{{
  "scenes": [
    {{
      "scene_ref": "scene ref from input",
      "milestones": [
        {{
          "title": "short descriptive title",
          "description": "max 6 concise sentences",
          "boundary_type": "begin|end|none",          
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



Scenes payload:
{scenes_payload}


"""


# Used by: Architect Analyze job (`architect.analyze_instance`), post-discovery scene merge phase.
# Callsite: `app/tasks/architect_analysis.py::_run_scene_merge_phase`.
# Goal: Reduce discovered scenes to a maximum cap (10) by merging coherent scene groups
# using only scene title/description metadata, returning rewritten merged titles/descriptions
# plus source scene references so paragraph/text spans can be recomposed downstream.
ARCHITECT_SCENE_MERGE_PROMPT = """
You are an expert narrative analyst helping build a knowledge graph from long-form story text.

You will receive a list of candidate scenes with:
- scene_ref
- scene_name
- scene_description

The current scene list may be over-fragmented.
Your task is to merge candidate scenes into a smaller set of coherent narrative scenes.

MERGE GOAL
- Return at most 10 scenes.
- Use the minimum amount of merging required to reach at most 10 scenes.
- Prefer fewer, larger, coherent scenes, but do not destroy narrative structure.
- If the input already has 10 or fewer scenes, return the scenes unchanged except for minor title/description cleanup.

MERGE RULES
Merge adjacent scenes when they continue the same:
- time period
- location
- dominant named characters
- conversation, confrontation, journey, plan, objective, or conflict progression

Prefer merging adjacent scenes.
Merge non-adjacent scenes only if they are clearly part of the same interrupted situation and can be merged without breaking chronology.

DO NOT MERGE ACROSS HARD BOUNDARIES
Do not merge scenes if there is a clear:
- time jump
- location change
- major change in dominant participating characters
- completed conflict followed by a new unrelated objective
- switch from one independent conversation/action sequence to another

WRITING RULES
- Do not invent facts, characters, places, motives, or outcomes.
- Do not just concatenate titles or descriptions.
- Rewrite each merged scene title and description so they summarize the full merged situation.
- Preserve the meaningful state changes from all source scenes.
- Merged scene title must include at least one explicit proper-name entity from the source scene titles/descriptions.
- Descriptions must use explicit named entities when available.
- Avoid generic labels such as "the group", "the party", "the travelers", "the companions", "the foreigners", "the man", "the woman", "the squire", or "the commander" when a proper name is available.
- If no proper name is available, use the most specific textual anchor from the source scenes.



Return STRICT RFC8259 JSON only:
{{
  "scenes": [
    {{
      "scene_ref": "new merged scene ref (string)",
      "name": "rewritten scene title",
      "description": "rewritten merged description",
      "source_scene_refs": ["original_scene_ref_1", "original_scene_ref_2"]
    }}
  ]
}}

CONSTRAINTS
- Output max 10 scenes.
- Every original scene_ref from input must appear in exactly one output scene's source_scene_refs.
- source_scene_refs must be non-empty.
- Preserve chronological order.
- Use double quotes and valid JSON only.
- Do not include markdown fences or text outside JSON.

Scenes payload:
{scenes_payload}
"""


# Used by: Architect Analyze job (`architect.analyze_instance`), post-merge scene rewrite phase.
# Callsite: `app/tasks/architect_analysis.py::_run_scene_rewrite_phase`.
# Goal: Rewrite scene descriptions/text to replace vague references with explicit names when grounded.
ARCHITECT_SCENE_REWRITE_PROMPT = """
You are an expert narrative editor for knowledge graph preparation.

You will receive scenes with:
- scene_ref
- scene_name
- scene_description
- scene_text

Task:
- Rewrite `scene_description` and `scene_text` to remove vague references when explicit names exist in the scene text.
- Prefer explicit names (e.g., "Thomas") over vague phrases (e.g., "a boy", "he", "they") when unambiguous.
- Do not invent names or events.
- If ambiguous, keep a neutral explicit phrase (e.g., "an unidentified woman").
- Keep scene meaning and chronology unchanged.
- Keep rewritten description concise (3-6 sentences).

Return STRICT RFC8259 JSON only:
{
  "scenes": [
    {
      "scene_ref": "scene ref from input",
      "scene_description": "rewritten description",
      "scene_text": "rewritten text"
    }
  ]
}

Scenes payload:
{scenes_payload}
"""


# Used by: Architect Generate job (`architect.generate_entities`), entity enrichment/update step.
# Callsite: `app/jobs/architect/entity_generator.py::_extract_properties_and_relationships`.
# Goal: Produce strict delta updates for properties/relationships and a full rewritten
# autogenerated summary using only scene-bounded evidence and allowed relationship targets.
ARCHITECT_PROPERTY_UPDATE_PROMPT = """You are extracting ontology data for an entity using only provided evidence.


You are an expert narrative analyst helping build a knowledge graph from long-form story text.


Your task is to decide which properties and relationships of a given entity from the provided scenes should be added to or matched against the persistent world graph.
For existing entities, only update the properties and relationships if there is a meaningful change or addition, otherwise return an empty update.
For new entities, return the properties and relationships that are clearly supported by the text and are meaningful enough to be added to the world graph.

I am giving you the context of each entity, including a summary, existing properties and relationships, catalogs of possible properties and relationships to choose from, 
allowed relationship targets, and relevant text chunks from the scenes.


Rules:
- Do not invent facts.
- Return only property values that are NEW or CHANGED.
- Return only relationships that are NEW or CHANGED.
- Choose properties by property_name from the properties catalog.
- Choose relationships by relationship_name from the relationships catalog.
- relationship_target must be an entity ID from the allowed related_entities list.
- For each relationship, obey destination type constraints in the relationships catalog.
- updated_autogenerated_summary must be a full rewritten summary, not an append/merge.



Entity context:
- entity_alias: {entity_alias}
- entity_type_name: {entity_type_name}

Existing entity summary:
{existing_autogenerated_text}

Existing entity properties:
{existing_properties}

Existing entity relationships:
{existing_relationships}

Properties that can be updated/added (from properties catalog):
{properties_catalog}

Relationships that can be updated/added (from relationships catalog):
{relationships_catalog}

Allowed relationship targets (explicit name/id/type):
{related_entities}

Scenes context:
{scenes_context}

Relevant text chunks:
[TEXT_CHUNKS]
{combined_chunks}
[/TEXT_CHUNKS]



Return STRICT RFC8259 JSON:
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
  "updated_autogenerated_summary": "re-write the existing autogenerated summary to reflect the new information, or keep it the same if there is no meaningful update. Always return a full rewritten summary, not an append/merge."
}}
"""
