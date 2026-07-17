from __future__ import annotations

from types import SimpleNamespace

from app.graphrag.semantic_v2.chunking import LosslessTokenChunker
from app.graphrag.semantic_v2.renderers import SemanticDocumentRenderer


class _WhitespaceTokenizer:
    def encode(self, text, *, add_special_tokens=True, truncation=False):
        assert truncation is False
        ids = list(range(len(str(text).split())))
        return ([-1] + ids + [-2]) if add_special_tokens else ids

    def decode(self, ids, *, skip_special_tokens=True):
        del skip_special_tokens
        return " ".join(f"token{value}" for value in ids if value >= 0)


def test_lossless_chunker_never_requests_tokenizer_truncation(monkeypatch):
    tokenizer = _WhitespaceTokenizer()
    monkeypatch.setattr(
        "app.graphrag.semantic_v2.chunking.get_embedding_model",
        lambda: SimpleNamespace(tokenizer=tokenizer),
    )
    chunker = LosslessTokenChunker(target_tokens=32, overlap_tokens=4)

    chunks = chunker.split(" ".join(f"word{i}" for i in range(100)))

    assert len(chunks) > 1
    assert all(chunker.token_count(f"passage: {chunk}") <= 32 for chunk in chunks)


class _RendererChunker:
    target_tokens = 384

    @staticmethod
    def token_count(text: str) -> int:
        return len(text.split())

    @staticmethod
    def split(text: str, *, header: str = "") -> list[str]:
        del header
        words = text.split()
        return [" ".join(words[index:index + 10]) for index in range(0, len(words), 10)]


def test_entity_renderer_keeps_neighbourhood_out_of_profile_and_chunks_long_text():
    renderer = SemanticDocumentRenderer(_RendererChunker(), long_text_threshold=12)
    definition = {
        "id": 7,
        "ontology_id": 3,
        "name": "Knight",
        "description": "A sworn warrior.",
        "properties": [{"id": 9, "name": "Title", "data_type": "text"}],
        "relationships": [{"id": 2, "name": "guards", "destination_name": "Kingdom"}],
    }
    node = {
        "entity_instance_id": "entity-valens",
        "instance_id": "instance-1",
        "ontology_id": 3,
        "entity_definition_id": 7,
        "alias": "Valens",
        "text": " ".join(f"history{i}" for i in range(50)),
        "properties": '{"9": "Royal Guard"}',
    }

    documents = renderer.entity(node, definition)

    profile = next(document for document in documents if document.source_kind == "entity_profile")
    chunks = [document for document in documents if document.source_kind == "entity_text_chunk"]
    assert "Valens" in profile.display_text
    assert "Royal Guard" in profile.display_text
    assert "guards" not in profile.display_text
    assert "Scene" not in profile.display_text
    assert len(chunks) == 5
    assert {document.source_field for document in chunks} == {"text"}


def test_entity_renderer_strips_html_before_chunking_and_embedding():
    renderer = SemanticDocumentRenderer(_RendererChunker(), long_text_threshold=512)
    documents = renderer.entity({
        "entity_instance_id": "entity-ernst", "instance_id": "instance-1",
        "ontology_id": 3, "entity_definition_id": 7, "alias": "Ernst",
        "text": '<p>Met <a href="/content/johnny" data-entity-alias="Johnny">Johnny</a>.</p>',
        "properties": {},
    }, {
        "id": 7, "ontology_id": 3, "name": "Character", "properties": [],
    })
    profile = documents[0]
    assert "Met Johnny." in profile.display_text
    assert "href" not in profile.display_text
    assert "data-entity-alias" not in profile.embedding_text


def test_scene_and_milestone_render_only_direct_graph_context():
    renderer = SemanticDocumentRenderer(_RendererChunker(), long_text_threshold=512)
    scene = renderer.scene({
        "id": "scene-1", "instance_id": "instance-1", "ontology_id": 3,
        "name": "The Gate", "description": "Valens refuses entry.",
        "derived_id": "source-1", "derived_alias": "Chronicle", "derived_type_name": "Story",
        "related_entities": [{"id": "valens", "alias": "Valens"}],
    })
    milestone = renderer.milestone({
        "id": "milestone-1", "scene_id": "scene-1", "scene_name": "The Gate",
        "instance_id": "instance-1", "ontology_id": 3, "name": "Refusal",
        "description": "The guard refuses the order.", "temporal_type": "other",
        "boundary_type": "none", "derived_id": "source-1",
        "related_entities": [{"id": "valens", "alias": "Valens"}],
    })

    assert scene.related_entity_ids == ["valens"]
    assert "Chronicle (Story)" in scene.display_text
    assert milestone.scene_id == "scene-1"
    assert "Containing scene: The Gate" in milestone.display_text
