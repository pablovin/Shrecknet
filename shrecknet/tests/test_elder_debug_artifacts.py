import json

from app.jobs.elder.debug_artifacts import ElderDebugArtifacts
from app.jobs.elder.schemas import ElderQueryResponse, ElderRetrievalPlan


def test_elder_debug_artifacts_write_ordered_responses_and_manifest(tmp_path):
    artifacts = ElderDebugArtifacts(tmp_path)
    artifacts.write(
        "retrieval planner llm",
        input={"prompt": "plan this"},
        output={"raw": '{"answer_goal":"x"}'},
    )
    artifacts.write(
        "elder response",
        input={"query": "question"},
        output={"answer": "answer"},
    )
    response = ElderQueryResponse(
        agent_id="elder-1",
        query="question",
        answer="answer",
        retrieval_plan=ElderRetrievalPlan(
            answer_goal="answer the question",
            response_scope="standard",
            evidence_budget_tokens=10_000,
        ),
        trace_id="trace-1",
    )
    final_response_path = artifacts.write_final_response(response)
    manifest_path = artifacts.write_manifest(trace_id="trace-1")

    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == [
        "01_retrieval_planner_llm.json",
        "02_elder_response.json",
        "final_response.json",
        "manifest.json",
    ]
    planner = json.loads((tmp_path / files[0]).read_text(encoding="utf-8"))
    assert planner["output"]["raw"] == '{"answer_goal":"x"}'
    final_response = json.loads((tmp_path / "final_response.json").read_text(encoding="utf-8"))
    assert final_response == response.model_dump(mode="json")
    assert final_response_path
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pipeline_version"] == "elder-query-retrieval-v2"
    assert manifest["trace_id"] == "trace-1"
    assert manifest_path


def test_disabled_elder_debug_artifacts_do_not_write():
    artifacts = ElderDebugArtifacts.create(enabled=False)
    assert artifacts.write("step", input={}, output={}) is None
    assert artifacts.write_final_response({"answer": "answer"}) is None
    assert artifacts.write_manifest() is None
