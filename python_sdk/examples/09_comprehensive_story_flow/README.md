# Comprehensive Story Flow (SDK)

This package runs a full end-to-end flow:

1. Register/login user
2. Create ontology and core world setup
3. Create one adventure
4. Run Novelist from PDF and create a story from draft
5. Run Architect analyze on the story
6. Approve all proposals and run Architect generate
7. Ask Elder questions

## Prerequisites

- Running Shrecknet API (`SHRECKNET_BASE_URL`, default `http://localhost:8100`)
- Working shreckLLM provider/model configuration
- Existing active agents:
  - `SHRECKNET_NOVELIST_AGENT_ID`
  - `SHRECKNET_ARCHITECT_AGENT_ID`
  - `SHRECKNET_ELDER_AGENT_ID`
- A local PDF path in `SHRECKNET_PDF_PATH`

## Required environment variables

- `SHRECKNET_BASE_URL` (optional)
- `SHRECKNET_USERNAME`, `SHRECKNET_PASSWORD`, `SHRECKNET_EMAIL` (optional defaults exist)
- `SHRECKNET_NOVELIST_AGENT_ID` (required)
- `SHRECKNET_ARCHITECT_AGENT_ID` (required)
- `SHRECKNET_ELDER_AGENT_ID` (required)
- `SHRECKNET_PDF_PATH` (required)

## Run order

```bash
python python_sdk/examples/09_comprehensive_story_flow/01_register_and_login.py
python python_sdk/examples/09_comprehensive_story_flow/02_create_world_ontology_and_core_entities.py
python python_sdk/examples/09_comprehensive_story_flow/03_create_adventure.py
python python_sdk/examples/09_comprehensive_story_flow/04_novelist_from_pdf_and_create_story.py
python python_sdk/examples/09_comprehensive_story_flow/05_architect_analyze_story.py
python python_sdk/examples/09_comprehensive_story_flow/06_architect_approve_and_generate.py
python python_sdk/examples/09_comprehensive_story_flow/07_elder_questions.py
```

## State handoff

Scripts share IDs via:

- `python_sdk/examples/09_comprehensive_story_flow/.state.json`

## Troubleshooting

- Invalid PDF path: ensure `SHRECKNET_PDF_PATH` exists and points to a `.pdf`
- Agent mismatch: verify each agent is active and has correct job type
- Timeouts: increase timeouts in scripts for slow environments
