import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.book_map import BookMapV4
from app.schemas.shreckworld import BookMapRead
from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage, NormalizedSection


FIXTURES = Path(__file__).parent / "fixtures" / "bookmap_v4" / "contracts"


def test_golden_v3_and_v4_contracts_validate_and_round_trip():
    v3_payload = json.loads((FIXTURES / "book_map_v3.json").read_text())
    v4_payload = json.loads((FIXTURES / "book_map_v4.json").read_text())
    assert BookMapRead.model_validate(v3_payload).model_dump() == v3_payload
    parsed = BookMapV4.model_validate(v4_payload)
    assert parsed.model_dump(mode="json") == v4_payload


@pytest.mark.parametrize("field,value", [
    ("node_type", "chapterish"),
    ("resolution_status", "approved"),
])
def test_v4_rejects_unknown_node_types_and_resolution_states(field, value):
    payload = json.loads((FIXTURES / "book_map_v4.json").read_text())
    payload["nodes"][0][field] = value
    with pytest.raises(ValidationError):
        BookMapV4.model_validate(payload)


def test_v4_rejects_invalid_digest_and_confidence():
    payload = json.loads((FIXTURES / "book_map_v4.json").read_text())
    payload["source_sha256"] = "not-a-digest"
    with pytest.raises(ValidationError):
        BookMapV4.model_validate(payload)
    payload["source_sha256"] = "a" * 64
    payload["nodes"][0]["parent_confidence"] = 1.2
    with pytest.raises(ValidationError):
        BookMapV4.model_validate(payload)


def test_normalized_legacy_positional_constructors_keep_defaults():
    page = NormalizedPage("p1", 1, "i")
    section = NormalizedSection("s1", None, "Notes", 1, ["Notes"], [], [1], ["i"])
    block = NormalizedBlock("b1", "s1", "paragraph", "Synthetic text")
    document = NormalizedDocument([page], [section], [block])
    assert page.accounting is None
    assert section.evidence_ids == [] and section.source_kind is None
    assert block.evidence_ids == [] and block.table_structure is None
    assert document.evidence == [] and document.page_accounting == []
