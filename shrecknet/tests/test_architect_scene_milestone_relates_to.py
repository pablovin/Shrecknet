import pytest
from app.jobs.architect.architect_v2 import ArchitectOrchestratorV2
from app.jobs.architect.schemas import SceneMilestoneProposalResponse, RelatesToProposalResponse

@pytest.mark.asyncio
async def test_coerce_scene_milestones_enforces_begin_end():
    orch = ArchitectOrchestratorV2()
    milestones = [
        {"name": "Middle", "boundary_type": "none"},
        {"name": "End", "boundary_type": "end"},
    ]
    result = orch._coerce_scene_milestones(milestones)
    assert any(m["boundary_type"] == "begin" for m in result)
    assert any(m["boundary_type"] == "end" for m in result)


def test_parse_relates_to_response_drops_ambiguous():
    orch = ArchitectOrchestratorV2()
    response = '{"proposals": [{"source": "A", "target": "B", "confidence": 0.5, "ambiguous": true}]}'
    parsed = orch._parse_relates_to_response(response)
    assert isinstance(parsed, RelatesToProposalResponse)
    assert len(parsed.proposals) == 0
