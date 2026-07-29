"""All Elder LLM prompts, ordered by execution.

Active v2 order:
1. ``V2_RETRIEVAL_PLANNER_PROMPT`` is used by ``planner.create_retrieval_plan``.
   It must return a JSON ``RetrievalPlan`` with ``answer_goal`` and 1-5 steps.
2. ``V2_SYNTHESIS_PROMPT`` is used by ``ElderQueryV2._synthesize_v2`` when all
   complete evidence fits. It returns neutral English citation-bearing blocks.
3. ``V2_OVERFLOW_EVIDENCE_PROMPT`` is used once per complete-record overflow
   batch. It returns an exhaustive citation-bearing evidence memorandum.
4. ``V2_OVERFLOW_FINAL_PROMPT`` combines those memoranda into neutral blocks.
5. The shared constrained character renderer translates and applies agent voice;
   code restores citations afterward.

"""


V2_RETRIEVAL_PLANNER_PROMPT = """You are the Elder evidence-retrieval planner.

Your task is to determine which canonical evidence must be retrieved to answer
the user. Do not answer the question.

Return ONLY valid JSON. Produce between 1 and 5 topologically ordered steps.

GENERAL RULES

1. Plan evidence retrieval, not final prose.
1a. Detect `target_language` only from USER QUERY and return a normalized
    BCP-47 tag such as `en`, `pt-BR`, or `fr`. Ignore grounding, conversation,
    ontology, and RPG terminology when detecting it; use `und` only when the
    query language cannot be determined.
2. Reuse resolved entity names when they are already supplied with high confidence.
3. Use resolve_entity for named world objects such as people, places, factions,
   objects, or narrative records.
4. Use resolve_concept for ontology-level concepts such as "story", "location",
   "chapter", "relationship", or world-specific categories.
5. Use exact_lookup for exact aliases, property values, or known terms.
6. Use hybrid_search for semantic actions, motivations, conflicts, themes,
   descriptions, or paraphrased narrative content.
7. Use select_nodes for structurally selecting canonical EntityInstances,
   Scenes, or Milestones by resolved names, ontology definitions, properties,
   provenance, or temporal criteria.
8. Use traverse_graph for canonical relationships, containment, provenance,
   shared participation, and graph neighbourhoods.
9. Use expand_temporal_context for latest, earliest, before, after, so-far,
   sequence, evolution, and timeline questions.
   Set temporal.ordering to recency only when time ordering is needed. Recency
   compares updated_at first and created_at second across sources. Choose
   temporal.direction from the requested presentation order. Do not infer
   cross-source chronology from FOLLOWED_BY or PRECEDED_BY relationships.
   For an unspecified recent-history window, a limit near 10 is usually useful,
   but choose the minimum sufficient limit for the actual question.
10. Select the minimum sufficient sources. Selected sources are hydrated in full
    after retrieval; do not plan excerpt windows or per-source token truncation.
11. Ontology-definition results help interpret the query but are not narrative evidence.
12. Prefer canonical graph selection for structural and temporal questions.
    Prefer graph-constrained hybrid search for implicit actions and meanings.
13. Execution applies the authorized ontology and world scope automatically.
14. Do not issue several broad searches when one graph-constrained search is sufficient.
15. bounded_read_cypher is exceptional. Use it only when the supported operations
    cannot express the required retrieval.
16. Generated Cypher must be read-only, parameterized, return canonical nodes or
    scoped parents as node, and contain an explicit LIMIT. Execution adds scope.
17. The final step or steps must produce evidence that can be sent directly to synthesis.

Return exactly this structure:
{{
  "answer_goal": "Specific factual or analytical result the synthesis must establish",
  "target_language": "BCP-47 language tag detected only from USER QUERY, or und",
  "response_scope": "brief|standard|deep",
  "query_intent": {{
    "kind": "fact|summary|relationship|timeline|history|comparison|mixed",
    "temporal_scope": "none|latest|earliest|before|after|so_far|range|timeline",
    "requires_semantic_inference": true,
    "requires_graph_structure": true
  }},
  "steps": [
    {{
      "id": "stable_step_id",
      "purpose": "Why this evidence is required",
      "operation": "hybrid_search",
      "query": "Focused semantic or lexical retrieval query, or null",
      "inputs": [],
      "entity_refs": [],
      "filters": {{
        "source_kinds": []
      }},
      "temporal": {{
        "mode": "none",
        "anchor": null,
        "ordering": "relevance",
        "direction": "descending"
      }},
      "traversal": {{
        "relationships": [],
        "direction": "both",
        "depth": 0
      }},
      "target_data_type": "mixed",
      "limit": 20,
      "evidence_type": "brief_fact|relationship_or_local_event|standard_summary|timeline_or_history|deep_comparison_or_mixed|exhaustive|null",
      "cypher": null,
      "parameters": {{}}
    }}
  ]
}}

Allowed operation values:
resolve_entity, resolve_concept, exact_lookup, hybrid_search, select_nodes,
traverse_graph, expand_temporal_context, hydrate_sources.
Allowed target_data_type values: entity, scene, milestone, mixed, ontology_definition.

EVIDENCE BUDGET CLASSIFICATION

Set evidence_type only on terminal steps whose results are sent to synthesis.
All resolution, constraint, and intermediate steps must use null. Choose:
- brief_fact for a narrowly scoped fact (12,000 tokens)
- relationship_or_local_event for a relationship or one local event (20,000)
- standard_summary for an ordinary bounded summary (35,000)
- timeline_or_history for chronological or historical coverage (60,000)
- deep_comparison_or_mixed for deep comparison or mixed analysis (100,000)
- exhaustive only when the user explicitly requests exhaustive coverage (100,000)

EXPECTED PLANNING BEHAVIOUR

Retrieve the minimum sufficient evidence. Broad phrases such as "what can you
tell me" do not request complete history. Prefer a bounded entity profile and
graph context, keep resolved-entity overviews to at most two retrieval steps,
and order the most useful sources first.

For "What happened in the last story?": resolve the Story concept; select the
latest canonical Story using ontology and temporal constraints; traverse to its
derived Scenes and contained Milestones; then hydrate the complete sources.

For "What did Valens take from the Mindflayers?": reuse high-confidence resolved
entities when supplied; otherwise resolve them. Run one graph-constrained hybrid
search over Scenes and Milestones for taking, stealing, recovering, receiving,
or removing something; traverse provenance and temporal neighbours around the
best results; then hydrate the relevant canonical sources.

For "What happened to Ernst lately?": resolve Ernst, then use
expand_temporal_context with mode latest, ordering recency, direction descending,
and a limit near 10 unless the question implies a narrower or broader window.

For "How has Ernst changed over the available records?": resolve Ernst, then use
expand_temporal_context with ordering recency and direction ascending so synthesis
receives the records from older to newer.

For non-temporal questions, keep ordering relevance. Do not request recency merely
because retrieved nodes happen to have timestamps.

USER QUERY:
{query}

GROUNDING:
{grounding_json}
"""


V2_GROUNDING_RULES_PROMPT = """Produce a neutral, concise factual answer in English.
Use only supplied evidence.
Distinguish canonical facts, narrative-supported events, interpretations,
contradictions, and unknowns. Split the complete answer into atomic, independently
supportable claims. Each claim must express one fact, conclusion, or user-facing
uncertainty and attach all supporting evidence IDs. Use only
supplied evidence_id values. Put every limitation and uncertainty in an answer
claim; `uncertainty` is optional metadata and cannot replace user-facing text.
Never claim evidence was absent when it is present. Do not apply personality,
voice, humour, rapport, roleplay, or target-language behavior.

Return exactly:
{"claims":[{"id":"claim-1","text":"English atomic factual claim","citations":["evidence-1"]}],"uncertainty":null}"""


V2_SYNTHESIS_PROMPT = """{rules}

QUERY:
{query}

CONVERSATION:
{conversation_json}

COMPLETE EVIDENCE:
{evidence_block}
"""


V2_OVERFLOW_EVIDENCE_PROMPT = """{rules}

QUERY:
{query}

CONVERSATION:
{conversation_json}

This is complete evidence batch {batch_number}/{batch_count}.
Produce an exhaustive citation-bearing evidence memorandum; do not give the final answer.

COMPLETE EVIDENCE:
{evidence_block}
"""


V2_OVERFLOW_FINAL_PROMPT = """{rules}

QUERY:
{query}

CONVERSATION:
{conversation_json}

EVIDENCE MEMORANDA:
{memoranda_block}
"""
