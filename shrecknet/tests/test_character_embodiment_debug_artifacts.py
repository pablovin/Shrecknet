from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts


def test_embodiment_debug_artifacts_keep_each_bundle_in_one_log(tmp_path) -> None:
    artifacts = EmbodimentDebugArtifacts(tmp_path)
    artifacts.write_call(
        source_index=0, source_alias="The First Source", stage="character incorporation",
        prompt="system prompt", payload={"scene": "input"}, raw_output='{"answer": 1}',
        parsed_output={"answer": 1}, model="test-model", usage_tag="test.call",
    )
    artifacts.write_call(
        source_index=0, source_alias="The First Source", stage="profile updates",
        prompt="update prompt", payload={"evidence": ["scene:1"]}, raw_output="{}",
        error=[{"message": "invalid"}], model="test-model", usage_tag="test.update",
        call_kind="semantic_correction",
    )
    artifacts.write_final(input={"initial": "state"}, output={"justification": "kept"})

    bundle = (tmp_path / "bundle_001_the_first_source.log").read_text(encoding="utf-8")
    assert bundle.count('"record_type": "llm_call"') == 2
    assert '"prompt": "system prompt"' in bundle
    assert '"raw_output": "{\\\"answer\\\": 1}"' in bundle
    assert '"call_kind": "semantic_correction"' in bundle
    final = (tmp_path / "final_pipeline.log").read_text(encoding="utf-8")
    assert '"justification": "kept"' in final


def test_disabled_embodiment_debug_artifacts_do_not_write() -> None:
    artifacts = EmbodimentDebugArtifacts.create(enabled=False, draft_id="draft", revision=1)
    assert artifacts.output_dir is None
    artifacts.write_final(input={}, output={})
