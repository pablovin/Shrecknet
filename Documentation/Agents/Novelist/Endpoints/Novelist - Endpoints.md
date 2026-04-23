# Novelist Agent Endpoints (Current API)

This document reflects the implemented Novelist routes in:

- shrecknet/app/api/routers/novelist.py

Router prefix:

- /jobs/novelist

Authentication:

- All endpoints require authenticated user context.

## 1. Start Run

Endpoint:

- POST /jobs/novelist/{agent_id}/runs

Purpose:

- Creates a Novelist run and starts async generation.

Request body (NovelistRunCreate):

```json
{
  "unstructured_text": "Raw narrative/session text",
  "language": "en",
  "instructions": "Keep names consistent",
  "previous_session_id": "entity-123"
}
```

Response:

- 202 Accepted
- NovelistRunRead

## 2. Start Run from Upload

Endpoint:

- POST /jobs/novelist/{agent_id}/runs/upload

Form-data fields:

- file (required): .txt or .pdf
- language (optional)
- instructions (optional)
- previous_session_id (optional)

Response:

- 202 Accepted
- NovelistRunRead

## 3. Get Run

Endpoint:

- GET /jobs/novelist/runs/{run_id}

Response:

- 200 OK
- NovelistRunRead

## 4. List Runs by Agent

Endpoint:

- GET /jobs/novelist/{agent_id}/runs?limit=20&offset=0

Query params:

- limit: integer, min 1, max 100, default 20
- offset: integer, min 0, default 0

Response:

- 200 OK
- list of NovelistRunRead

## 5. Delete Run

Endpoint:

- DELETE /jobs/novelist/{agent_id}/runs/{run_id}

Response:

```json
{
  "deleted": 1
}
```

## Response Sections for Frontend

NovelistRunRead includes these sectioned step outputs:

- step_outputs.step_1: scenes, milestones, related entities
- step_outputs.step_2: scene writing packages
- step_outputs.step_3: narrative context per scene
- step_outputs.step_4: scene intended draft output per scene
- step_outputs.step_5: scene prose output per scene
- step_outputs.step_6: critic response
- step_outputs.step_7: final rewritten full text

Final text field:

- step_outputs.step_7.final_rewritten_text

Compatibility field:

- draft_text (same final merged content)

## Realistic NovelistRunRead Example (All Fields)

```json
{
  "id": "ec89fbbf-1b52-4e45-b8f5-a53f34ec60d1",
  "agent_id": "a02ca8cc-606f-4f4f-9e7f-1f37d2ab8e84",
  "background_job_id": 512,
  "ontology_id": null,
  "ontology_instance_id": null,
  "status": "completed",
  "stage": "done",
  "settings": {
    "requested_by": 7,
    "language": "en"
  },
  "request_payload": {
    "unstructured_text": "Aria and Brenn arrived at the city gate under torchlight...",
    "language": "en",
    "instructions": "Keep names and titles consistent.",
    "previous_session_id": "entity-123",
    "previous_session_text": null,
    "previous_session_summary": null
  },
  "artifacts": {
    "inputs": {
      "unstructured_text": "Aria and Brenn arrived at the city gate under torchlight...",
      "language": "en",
      "instructions": "Keep names and titles consistent.",
      "previous_session_id": "entity-123",
      "continuity_brief": "- Aria distrusts the council\n- Brenn protects her",
      "previous_session_summary": "- Aria distrusts the council\n- Brenn protects her"
    },
    "stages": {
      "scaffolding": {
        "scene_count": 2,
        "scenes": [
          {
            "scene_id": "scene-001",
            "name": "Gate Arrival",
            "scene_summary": "The party reaches the city gate.",
            "milestones": [
              "Gate challenge: The guards question the party."
            ],
            "related_entities": [
              "Aria",
              "Brenn"
            ],
            "source_anchors": [
              "P1-P2"
            ],
            "new_or_update": "new"
          },
          {
            "scene_id": "scene-002",
            "name": "Council Chamber",
            "scene_summary": "The council receives the party.",
            "milestones": [
              "Formal audience: The council demands an oath."
            ],
            "related_entities": [
              "Aria",
              "Brenn",
              "High Council"
            ],
            "source_anchors": [
              "P3-P5"
            ],
            "new_or_update": "new"
          }
        ]
      },
      "scene_package": {
        "count": 2,
        "packages": [
          {
            "scene_id": "scene-001",
            "source_paragraphs": [1, 2],
            "raw_scene_text": "Aria and Brenn arrived at the gate...",
            "scene_summary": "The party reaches the city gate.",
            "scene_goal": "Gain entry without surrendering leverage.",
            "milestones": [
              "Gate challenge: The guards question the party."
            ],
            "related_entities": [
              "Aria",
              "Brenn"
            ],
            "temporal_position_hint": "early",
            "tone_hint": "tense",
            "open_questions_for_retrieval": [
              "What prior event most affects this negotiation?"
            ],
            "new_or_update": "new"
          },
          {
            "scene_id": "scene-002",
            "source_paragraphs": [3, 4, 5],
            "raw_scene_text": "Inside the chamber, the elders demanded an oath...",
            "scene_summary": "The council receives the party.",
            "scene_goal": "Secure conditional support from the council.",
            "milestones": [
              "Formal audience: The council demands an oath."
            ],
            "related_entities": [
              "Aria",
              "Brenn",
              "High Council"
            ],
            "temporal_position_hint": "middle",
            "tone_hint": "political",
            "open_questions_for_retrieval": [
              "Which unresolved tension should influence the dialogue?"
            ],
            "new_or_update": "new"
          }
        ]
      },
      "retrieval": {
        "scene-001": {
          "queries": [
            "What prior event is most relevant to scene-001?"
          ],
          "bucket_counts": {
            "prior_events": 1,
            "relationship_summaries": 0,
            "personality_reminders": 1,
            "unresolved_tensions": 0,
            "style_details": 1,
            "contradiction_warnings": 0
          },
          "buckets": {
            "prior_events": [
              "Earlier, Aria refused the oath before the council."
            ],
            "relationship_summaries": [],
            "personality_reminders": [
              "Brenn's speaking style is blunt and confrontational."
            ],
            "unresolved_tensions": [],
            "style_details": [
              "Aria speaks in clipped, deliberate phrases under pressure."
            ],
            "contradiction_warnings": []
          }
        },
        "scene-002": {
          "queries": [
            "Which unresolved tension should influence scene-002?"
          ],
          "bucket_counts": {
            "prior_events": 1,
            "relationship_summaries": 1,
            "personality_reminders": 0,
            "unresolved_tensions": 1,
            "style_details": 0,
            "contradiction_warnings": 1
          },
          "buckets": {
            "prior_events": [
              "The prior hearing ended without oath resolution."
            ],
            "relationship_summaries": [
              "Council trust in Aria remains fractured."
            ],
            "personality_reminders": [],
            "unresolved_tensions": [
              "The elders suspect the party of withholding terms."
            ],
            "style_details": [],
            "contradiction_warnings": [
              "Do not claim the treaty was already signed."
            ]
          }
        }
      },
      "intent_drafting": {
        "scene-001": {
          "scene_id": "scene-001",
          "what_happens": [
            "The party negotiates entry at the gate."
          ],
          "emotional_progression": [
            "guarded",
            "defiant"
          ],
          "speaking_goals": [
            "gain entry",
            "avoid confession"
          ],
          "implied_history": [
            "Old betrayal is remembered."
          ],
          "forbidden_contradictions": [
            "Do not claim the oath dispute is resolved."
          ]
        },
        "scene-002": {
          "scene_id": "scene-002",
          "what_happens": [
            "The council hears the request and imposes terms."
          ],
          "emotional_progression": [
            "contained",
            "confrontational"
          ],
          "speaking_goals": [
            "secure conditional support"
          ],
          "implied_history": [
            "The oath conflict informs every reply."
          ],
          "forbidden_contradictions": [
            "Do not fully resolve faction conflict."
          ]
        }
      },
      "prose_generation": [
        {
          "scene_id": "scene-001",
          "name": "Gate Arrival",
          "scene_summary": "The party reaches the city gate.",
          "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms.</p>"
        },
        {
          "scene_id": "scene-002",
          "name": "Council Chamber",
          "scene_summary": "The council receives the party.",
          "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>"
        }
      ],
      "critic": {
        "global_notes": [
          "Transitions between scene-001 and scene-002 should be smoother."
        ],
        "by_scene": {
          "scene-001": {
            "continuity_issues": [],
            "duplication": [],
            "missing_transitions": [
              "Add one bridge sentence to council entrance."
            ],
            "voice_drift": [],
            "pacing": [],
            "graph_contradictions": [],
            "exposition_problems": []
          },
          "scene-002": {
            "continuity_issues": [],
            "duplication": [],
            "missing_transitions": [],
            "voice_drift": [],
            "pacing": [],
            "graph_contradictions": [],
            "exposition_problems": []
          }
        }
      },
      "revision": {
        "scenes": [
          {
            "scene_id": "scene-001",
            "name": "Gate Arrival",
            "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p>",
            "merged_from": [
              "scene-001"
            ],
            "split_from": null,
            "notes": [
              "Added transition bridge."
            ]
          },
          {
            "scene_id": "scene-002",
            "name": "Council Chamber",
            "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
            "merged_from": [
              "scene-002"
            ],
            "split_from": null,
            "notes": []
          }
        ],
        "lineage": {
          "scene-001": {
            "source_scene_ids": [
              "scene-001"
            ],
            "action": "kept"
          },
          "scene-002": {
            "source_scene_ids": [
              "scene-002"
            ],
            "action": "kept"
          }
        },
        "global_revision_notes": [
          "Applied critic transition note to scene-001."
        ]
      },
      "merging": {
        "scene_count": 2,
        "final_text": "<h2>Scene 1: Gate Arrival</h2><p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p><h2>Scene 2: Council Chamber</h2><p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>"
      }
    },
    "scene_progress": {
      "scene-001": {
        "intent_done": true,
        "prose_done": true,
        "critic_issue_count": 1,
        "revision_action": "kept"
      },
      "scene-002": {
        "intent_done": true,
        "prose_done": true,
        "critic_issue_count": 0,
        "revision_action": "kept"
      }
    },
    "timings_ms": {
      "scaffolding": 284.11,
      "scene_package": 137.92,
      "retrieval": 205.62,
      "intent_drafting": 164.47,
      "prose_generation": 316.28,
      "critic": 92.31,
      "revision": 84.09,
      "merging": 7.94,
      "total": 1292.74
    },
    "models": {},
    "step_outputs": {
      "step_1": {
        "label": "scene_scaffolding",
        "scenes": [
          {
            "scene_id": "scene-001",
            "name": "Gate Arrival",
            "scene_summary": "The party reaches the city gate.",
            "milestones": [
              "Gate challenge: The guards question the party."
            ],
            "related_entities": [
              "Aria",
              "Brenn"
            ],
            "source_anchors": [
              "P1-P2"
            ],
            "new_or_update": "new"
          },
          {
            "scene_id": "scene-002",
            "name": "Council Chamber",
            "scene_summary": "The council receives the party.",
            "milestones": [
              "Formal audience: The council demands an oath."
            ],
            "related_entities": [
              "Aria",
              "Brenn",
              "High Council"
            ],
            "source_anchors": [
              "P3-P5"
            ],
            "new_or_update": "new"
          }
        ]
      },
      "step_2": {
        "label": "scene_writing_packages",
        "scene_packages": [
          {
            "scene_id": "scene-001",
            "source_paragraphs": [1, 2],
            "raw_scene_text": "Aria and Brenn arrived at the gate...",
            "scene_summary": "The party reaches the city gate.",
            "scene_goal": "Gain entry without surrendering leverage.",
            "milestones": [
              "Gate challenge: The guards question the party."
            ],
            "related_entities": [
              "Aria",
              "Brenn"
            ],
            "temporal_position_hint": "early",
            "tone_hint": "tense",
            "open_questions_for_retrieval": [
              "What prior event most affects this negotiation?"
            ],
            "new_or_update": "new"
          },
          {
            "scene_id": "scene-002",
            "source_paragraphs": [3, 4, 5],
            "raw_scene_text": "Inside the chamber, the elders demanded an oath...",
            "scene_summary": "The council receives the party.",
            "scene_goal": "Secure conditional support from the council.",
            "milestones": [
              "Formal audience: The council demands an oath."
            ],
            "related_entities": [
              "Aria",
              "Brenn",
              "High Council"
            ],
            "temporal_position_hint": "middle",
            "tone_hint": "political",
            "open_questions_for_retrieval": [
              "Which unresolved tension should influence the dialogue?"
            ],
            "new_or_update": "new"
          }
        ]
      },
      "step_3": {
        "label": "scene_narrative_context",
        "narrative_context_by_scene": [
          {
            "scene_id": "scene-001",
            "queries": [
              "What prior event is most relevant to scene-001?"
            ],
            "narrative_context": {
              "prior_events": [
                "Earlier, Aria refused the oath before the council."
              ],
              "relationship_summaries": [],
              "personality_reminders": [
                "Brenn's speaking style is blunt and confrontational."
              ],
              "unresolved_tensions": [],
              "style_details": [
                "Aria speaks in clipped, deliberate phrases under pressure."
              ],
              "contradiction_warnings": []
            }
          },
          {
            "scene_id": "scene-002",
            "queries": [
              "Which unresolved tension should influence scene-002?"
            ],
            "narrative_context": {
              "prior_events": [
                "The prior hearing ended without oath resolution."
              ],
              "relationship_summaries": [
                "Council trust in Aria remains fractured."
              ],
              "personality_reminders": [],
              "unresolved_tensions": [
                "The elders suspect the party of withholding terms."
              ],
              "style_details": [],
              "contradiction_warnings": [
                "Do not claim the treaty was already signed."
              ]
            }
          }
        ]
      },
      "step_4": {
        "label": "scene_intended_draft_output",
        "scene_intents": [
          {
            "scene_id": "scene-001",
            "what_happens": [
              "The party negotiates entry at the gate."
            ],
            "emotional_progression": [
              "guarded",
              "defiant"
            ],
            "speaking_goals": [
              "gain entry",
              "avoid confession"
            ],
            "implied_history": [
              "Old betrayal is remembered."
            ],
            "forbidden_contradictions": [
              "Do not claim the oath dispute is resolved."
            ]
          },
          {
            "scene_id": "scene-002",
            "what_happens": [
              "The council hears the request and imposes terms."
            ],
            "emotional_progression": [
              "contained",
              "confrontational"
            ],
            "speaking_goals": [
              "secure conditional support"
            ],
            "implied_history": [
              "The oath conflict informs every reply."
            ],
            "forbidden_contradictions": [
              "Do not fully resolve faction conflict."
            ]
          }
        ]
      },
      "step_5": {
        "label": "scene_prose_output",
        "scene_prose": [
          {
            "scene_id": "scene-001",
            "name": "Gate Arrival",
            "scene_summary": "The party reaches the city gate.",
            "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms.</p>"
          },
          {
            "scene_id": "scene-002",
            "name": "Council Chamber",
            "scene_summary": "The council receives the party.",
            "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>"
          }
        ]
      },
      "step_6": {
        "label": "critic_response",
        "critic": {
          "global_notes": [
            "Transitions between scene-001 and scene-002 should be smoother."
          ],
          "by_scene": {
            "scene-001": {
              "continuity_issues": [],
              "duplication": [],
              "missing_transitions": [
                "Add one bridge sentence to council entrance."
              ],
              "voice_drift": [],
              "pacing": [],
              "graph_contradictions": [],
              "exposition_problems": []
            },
            "scene-002": {
              "continuity_issues": [],
              "duplication": [],
              "missing_transitions": [],
              "voice_drift": [],
              "pacing": [],
              "graph_contradictions": [],
              "exposition_problems": []
            }
          }
        }
      },
      "step_7": {
        "label": "full_rewritten_text",
        "final_rewritten_text": "<h2>Scene 1: Gate Arrival</h2><p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p><h2>Scene 2: Council Chamber</h2><p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
        "revised_scenes": [
          {
            "scene_id": "scene-001",
            "name": "Gate Arrival",
            "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p>",
            "merged_from": [
              "scene-001"
            ],
            "split_from": null,
            "notes": [
              "Added transition bridge."
            ]
          },
          {
            "scene_id": "scene-002",
            "name": "Council Chamber",
            "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
            "merged_from": [
              "scene-002"
            ],
            "split_from": null,
            "notes": []
          }
        ],
        "lineage": {
          "scene-001": {
            "source_scene_ids": [
              "scene-001"
            ],
            "action": "kept"
          },
          "scene-002": {
            "source_scene_ids": [
              "scene-002"
            ],
            "action": "kept"
          }
        }
      }
    }
  },
  "previous_session_id": "entity-123",
  "previous_session_summary": "- Aria distrusts the council\n- Brenn protects her",
  "previous_session_lookup_status": "matched_entity_instance_id",
  "elder_qna_by_part": null,
  "scene_results": [
    {
      "scene_id": "scene-001",
      "order": 1,
      "scene_summary": "The party reaches the city gate.",
      "scene_goal": "Gain entry without surrendering leverage.",
      "source_paragraphs": [1, 2],
      "milestones": [
        "Gate challenge: The guards question the party."
      ],
      "related_entities": [
        "Aria",
        "Brenn"
      ],
      "retrieval": {
        "prior_events": [
          "Earlier, Aria refused the oath before the council."
        ],
        "relationship_summaries": [],
        "personality_reminders": [
          "Brenn's speaking style is blunt and confrontational."
        ],
        "unresolved_tensions": [],
        "style_details": [
          "Aria speaks in clipped, deliberate phrases under pressure."
        ],
        "contradiction_warnings": []
      },
      "intent": {
        "scene_id": "scene-001",
        "what_happens": [
          "The party negotiates entry at the gate."
        ],
        "emotional_progression": [
          "guarded",
          "defiant"
        ],
        "speaking_goals": [
          "gain entry",
          "avoid confession"
        ],
        "implied_history": [
          "Old betrayal is remembered."
        ],
        "forbidden_contradictions": [
          "Do not claim the oath dispute is resolved."
        ]
      },
      "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms.</p>",
      "critic": {
        "continuity_issues": [],
        "duplication": [],
        "missing_transitions": [
          "Add one bridge sentence to council entrance."
        ],
        "voice_drift": [],
        "pacing": [],
        "graph_contradictions": [],
        "exposition_problems": []
      },
      "critic_issue_count": 1,
      "revision_action": "kept",
      "lineage": {
        "source_scene_ids": [
          "scene-001"
        ],
        "action": "kept"
      }
    },
    {
      "scene_id": "scene-002",
      "order": 2,
      "scene_summary": "The council receives the party.",
      "scene_goal": "Secure conditional support from the council.",
      "source_paragraphs": [3, 4, 5],
      "milestones": [
        "Formal audience: The council demands an oath."
      ],
      "related_entities": [
        "Aria",
        "Brenn",
        "High Council"
      ],
      "retrieval": {
        "prior_events": [
          "The prior hearing ended without oath resolution."
        ],
        "relationship_summaries": [
          "Council trust in Aria remains fractured."
        ],
        "personality_reminders": [],
        "unresolved_tensions": [
          "The elders suspect the party of withholding terms."
        ],
        "style_details": [],
        "contradiction_warnings": [
          "Do not claim the treaty was already signed."
        ]
      },
      "intent": {
        "scene_id": "scene-002",
        "what_happens": [
          "The council hears the request and imposes terms."
        ],
        "emotional_progression": [
          "contained",
          "confrontational"
        ],
        "speaking_goals": [
          "secure conditional support"
        ],
        "implied_history": [
          "The oath conflict informs every reply."
        ],
        "forbidden_contradictions": [
          "Do not fully resolve faction conflict."
        ]
      },
      "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
      "critic": {
        "continuity_issues": [],
        "duplication": [],
        "missing_transitions": [],
        "voice_drift": [],
        "pacing": [],
        "graph_contradictions": [],
        "exposition_problems": []
      },
      "critic_issue_count": 0,
      "revision_action": "kept",
      "lineage": {
        "source_scene_ids": [
          "scene-002"
        ],
        "action": "kept"
      }
    }
  ],
  "step_outputs": {
    "step_1": {
      "label": "scene_scaffolding",
      "scenes": [
        {
          "scene_id": "scene-001",
          "name": "Gate Arrival",
          "scene_summary": "The party reaches the city gate.",
          "milestones": [
            "Gate challenge: The guards question the party."
          ],
          "related_entities": [
            "Aria",
            "Brenn"
          ],
          "source_anchors": [
            "P1-P2"
          ],
          "new_or_update": "new"
        },
        {
          "scene_id": "scene-002",
          "name": "Council Chamber",
          "scene_summary": "The council receives the party.",
          "milestones": [
            "Formal audience: The council demands an oath."
          ],
          "related_entities": [
            "Aria",
            "Brenn",
            "High Council"
          ],
          "source_anchors": [
            "P3-P5"
          ],
          "new_or_update": "new"
        }
      ]
    },
    "step_2": {
      "label": "scene_writing_packages",
      "scene_packages": [
        {
          "scene_id": "scene-001",
          "source_paragraphs": [1, 2],
          "raw_scene_text": "Aria and Brenn arrived at the gate...",
          "scene_summary": "The party reaches the city gate.",
          "scene_goal": "Gain entry without surrendering leverage.",
          "milestones": [
            "Gate challenge: The guards question the party."
          ],
          "related_entities": [
            "Aria",
            "Brenn"
          ],
          "temporal_position_hint": "early",
          "tone_hint": "tense",
          "open_questions_for_retrieval": [
            "What prior event most affects this negotiation?"
          ],
          "new_or_update": "new"
        },
        {
          "scene_id": "scene-002",
          "source_paragraphs": [3, 4, 5],
          "raw_scene_text": "Inside the chamber, the elders demanded an oath...",
          "scene_summary": "The council receives the party.",
          "scene_goal": "Secure conditional support from the council.",
          "milestones": [
            "Formal audience: The council demands an oath."
          ],
          "related_entities": [
            "Aria",
            "Brenn",
            "High Council"
          ],
          "temporal_position_hint": "middle",
          "tone_hint": "political",
          "open_questions_for_retrieval": [
            "Which unresolved tension should influence the dialogue?"
          ],
          "new_or_update": "new"
        }
      ]
    },
    "step_3": {
      "label": "scene_narrative_context",
      "narrative_context_by_scene": [
        {
          "scene_id": "scene-001",
          "queries": [
            "What prior event is most relevant to scene-001?"
          ],
          "narrative_context": {
            "prior_events": [
              "Earlier, Aria refused the oath before the council."
            ],
            "relationship_summaries": [],
            "personality_reminders": [
              "Brenn's speaking style is blunt and confrontational."
            ],
            "unresolved_tensions": [],
            "style_details": [
              "Aria speaks in clipped, deliberate phrases under pressure."
            ],
            "contradiction_warnings": []
          }
        },
        {
          "scene_id": "scene-002",
          "queries": [
            "Which unresolved tension should influence scene-002?"
          ],
          "narrative_context": {
            "prior_events": [
              "The prior hearing ended without oath resolution."
            ],
            "relationship_summaries": [
              "Council trust in Aria remains fractured."
            ],
            "personality_reminders": [],
            "unresolved_tensions": [
              "The elders suspect the party of withholding terms."
            ],
            "style_details": [],
            "contradiction_warnings": [
              "Do not claim the treaty was already signed."
            ]
          }
        }
      ]
    },
    "step_4": {
      "label": "scene_intended_draft_output",
      "scene_intents": [
        {
          "scene_id": "scene-001",
          "what_happens": [
            "The party negotiates entry at the gate."
          ],
          "emotional_progression": [
            "guarded",
            "defiant"
          ],
          "speaking_goals": [
            "gain entry",
            "avoid confession"
          ],
          "implied_history": [
            "Old betrayal is remembered."
          ],
          "forbidden_contradictions": [
            "Do not claim the oath dispute is resolved."
          ]
        },
        {
          "scene_id": "scene-002",
          "what_happens": [
            "The council hears the request and imposes terms."
          ],
          "emotional_progression": [
            "contained",
            "confrontational"
          ],
          "speaking_goals": [
            "secure conditional support"
          ],
          "implied_history": [
            "The oath conflict informs every reply."
          ],
          "forbidden_contradictions": [
            "Do not fully resolve faction conflict."
          ]
        }
      ]
    },
    "step_5": {
      "label": "scene_prose_output",
      "scene_prose": [
        {
          "scene_id": "scene-001",
          "name": "Gate Arrival",
          "scene_summary": "The party reaches the city gate.",
          "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms.</p>"
        },
        {
          "scene_id": "scene-002",
          "name": "Council Chamber",
          "scene_summary": "The council receives the party.",
          "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>"
        }
      ]
    },
    "step_6": {
      "label": "critic_response",
      "critic": {
        "global_notes": [
          "Transitions between scene-001 and scene-002 should be smoother."
        ],
        "by_scene": {
          "scene-001": {
            "continuity_issues": [],
            "duplication": [],
            "missing_transitions": [
              "Add one bridge sentence to council entrance."
            ],
            "voice_drift": [],
            "pacing": [],
            "graph_contradictions": [],
            "exposition_problems": []
          },
          "scene-002": {
            "continuity_issues": [],
            "duplication": [],
            "missing_transitions": [],
            "voice_drift": [],
            "pacing": [],
            "graph_contradictions": [],
            "exposition_problems": []
          }
        }
      }
    },
    "step_7": {
      "label": "full_rewritten_text",
      "final_rewritten_text": "<h2>Scene 1: Gate Arrival</h2><p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p><h2>Scene 2: Council Chamber</h2><p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
      "revised_scenes": [
        {
          "scene_id": "scene-001",
          "name": "Gate Arrival",
          "prose_html": "<p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p>",
          "merged_from": [
            "scene-001"
          ],
          "split_from": null,
          "notes": [
            "Added transition bridge."
          ]
        },
        {
          "scene_id": "scene-002",
          "name": "Council Chamber",
          "prose_html": "<p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
          "merged_from": [
            "scene-002"
          ],
          "split_from": null,
          "notes": []
        }
      ],
      "lineage": {
        "scene-001": {
          "source_scene_ids": [
            "scene-001"
          ],
          "action": "kept"
        },
        "scene-002": {
          "source_scene_ids": [
            "scene-002"
          ],
          "action": "kept"
        }
      }
    }
  },
  "timing_summary": {
    "total_ms": 1292.74,
    "by_stage_ms": {
      "scaffolding": 284.11,
      "scene_package": 137.92,
      "retrieval": 205.62,
      "intent_drafting": 164.47,
      "prose_generation": 316.28,
      "critic": 92.31,
      "revision": 84.09,
      "merging": 7.94,
      "total": 1292.74
    },
    "scene_count": 2,
    "retrieval_query_count": 2
  },
  "stage_timings": {
    "scaffolding": 284.11,
    "scene_package": 137.92,
    "retrieval": 205.62,
    "intent_drafting": 164.47,
    "prose_generation": 316.28,
    "critic": 92.31,
    "revision": 84.09,
    "merging": 7.94,
    "total": 1292.74
  },
  "scene_progress": {
    "scene-001": {
      "intent_done": true,
      "prose_done": true,
      "critic_issue_count": 1,
      "revision_action": "kept"
    },
    "scene-002": {
      "intent_done": true,
      "prose_done": true,
      "critic_issue_count": 0,
      "revision_action": "kept"
    }
  },
  "draft_text": "<h2>Scene 1: Gate Arrival</h2><p>Aria paused beneath the torchlight and measured every face before speaking.</p><p>Brenn answered the guards with clipped patience until the gates opened under strict terms, and by then the chamber summons was already waiting.</p><h2>Scene 2: Council Chamber</h2><p>In the chamber, the elders demanded an oath and silence settled like weight.</p><p>Aria refused surrender, Brenn held the line, and the council offered only conditional hearing.</p>",
  "critic_notes": "{\"global_notes\":[\"Transitions between scene-001 and scene-002 should be smoother.\"],\"by_scene\":{\"scene-001\":{\"continuity_issues\":[],\"duplication\":[],\"missing_transitions\":[\"Add one bridge sentence to council entrance.\"],\"voice_drift\":[],\"pacing\":[],\"graph_contradictions\":[],\"exposition_problems\":[]},\"scene-002\":{\"continuity_issues\":[],\"duplication\":[],\"missing_transitions\":[],\"voice_drift\":[],\"pacing\":[],\"graph_contradictions\":[],\"exposition_problems\":[]}}}",
  "error_message": null,
  "created_at": "2026-04-22T12:41:28.117236Z",
  "updated_at": "2026-04-22T12:41:30.705841Z"
}
```

## Common Errors

- 404 Agent not found
- 400 Agent is not active
- 400 Agent job type is not novelist
- 400 Unsupported file type for upload (only .txt or .pdf)
- 503 OpenAI API key not configured
