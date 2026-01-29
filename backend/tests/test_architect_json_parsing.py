"""Test JSON parsing robustness in architect analysis."""

import json
import pytest
from app.jobs.architect.architect import ArchitectOrchestrator


def test_fix_escaped_quotes_valid_json():
    """Test that valid JSON passes through unchanged."""
    valid_json = '{"key": "value", "array": ["item1", "item2"]}'
    result = ArchitectOrchestrator._fix_escaped_quotes(valid_json)
    # Should parse without error
    parsed = json.loads(result)
    assert parsed["key"] == "value"
    assert parsed["array"] == ["item1", "item2"]


def test_fix_escaped_quotes_double_escaped():
    """Test fixing double-escaped quotes like from LLM output."""
    # This simulates the actual malformed JSON from the error log
    # Note: In Python, we write r'...' or escape backslashes to represent literal backslashes
    malformed_json = r"""{
  "new_instances": [
    {
      "alias": "Wikka",
      "metadata": {
        "supporting_sentences": [
          \"In the modern nights of the twenty-first century, we were aided by formidable allies—the Wikka, the Technopriests, and dark creatures who decided to protect the world.\"
        ]
      }
    }
  ],
  "existing_instances": []
}"""

    result = ArchitectOrchestrator._fix_escaped_quotes(malformed_json)
    # Should now be parseable
    parsed = json.loads(result)
    assert "new_instances" in parsed
    assert len(parsed["new_instances"]) == 1
    assert parsed["new_instances"][0]["alias"] == "Wikka"
    # The supporting_sentences should be properly parsed
    sentences = parsed["new_instances"][0]["metadata"]["supporting_sentences"]
    assert len(sentences) == 1
    assert "In the modern nights" in sentences[0]


def test_extract_json_block():
    """Test JSON block extraction from LLM response."""
    response = """Here is the JSON:
{
  "new_instances": [],
  "existing_instances": []
}
That's all."""

    result = ArchitectOrchestrator._extract_json_block(response)
    parsed = json.loads(result)
    assert "new_instances" in parsed
    assert "existing_instances" in parsed


def test_extract_json_block_no_json():
    """Test that extraction fails gracefully when no JSON present."""
    response = "This is just text without any JSON"

    with pytest.raises(ValueError, match="No JSON object found"):
        ArchitectOrchestrator._extract_json_block(response)


def test_parse_llm_response_with_malformed_json(mocker):
    """Test that parse_llm_response handles malformed JSON gracefully."""
    from app.integrations.llm.openai_client import OpenAIClient
    from app.integrations.llm.model_policy import ModelPolicy

    # Mock dependencies
    llm_client = mocker.Mock(spec=OpenAIClient)
    model_policy = mocker.Mock(spec=ModelPolicy)
    graph_retriever = mocker.Mock()

    orchestrator = ArchitectOrchestrator(
        llm_client=llm_client,
        model_policy=model_policy,
        graph_retriever=graph_retriever,
    )

    # Test with completely malformed JSON
    malformed_response = "This is not JSON at all"

    result = orchestrator._parse_llm_response(
        malformed_response,
        chunk_index=0,
        chunk_text="test chunk",
        source_alias="test",
        source_definition_id=1,
    )

    # Should return empty result instead of crashing
    assert result.chunk_index == 0
    assert result.new_instances == []
    assert result.existing_instances == []


def test_parse_llm_response_with_valid_json(mocker):
    """Test that parse_llm_response works with valid JSON."""
    from app.integrations.llm.openai_client import OpenAIClient
    from app.integrations.llm.model_policy import ModelPolicy

    # Mock dependencies
    llm_client = mocker.Mock(spec=OpenAIClient)
    model_policy = mocker.Mock(spec=ModelPolicy)
    graph_retriever = mocker.Mock()

    orchestrator = ArchitectOrchestrator(
        llm_client=llm_client,
        model_policy=model_policy,
        graph_retriever=graph_retriever,
    )

    valid_response = """{
  "new_instances": [
    {
      "alias": "Test Entity",
      "entity_definition_id": 5,
      "confidence": 0.8,
      "justification": "Test justification"
    }
  ],
  "existing_instances": []
}"""

    result = orchestrator._parse_llm_response(
        valid_response,
        chunk_index=0,
        chunk_text="test chunk",
        source_alias="test",
        source_definition_id=1,
    )

    assert result.chunk_index == 0
    assert len(result.new_instances) == 1
    assert result.new_instances[0].alias == "Test Entity"
    assert result.new_instances[0].confidence == 0.8
    assert result.existing_instances == []
