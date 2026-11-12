from types import SimpleNamespace
from uuid import uuid4

from app.db.session import session_factory
from app.models import ShreckWorldDocument, ShreckWorldDocumentBookMap, ShreckWorldDocumentChunk, ShreckWorldDocumentSection
from app.services.book_map import build_book_map
from test_ontology_items import add, direct
from test_upload_metadata_api import api, upload


def test_book_map_builds_hierarchy_references_and_quality_deterministically():
    sections = [
        SimpleNamespace(id="s1", heading="Combat", heading_level=1, parent_section_id=None, heading_path=["Combat"]),
        SimpleNamespace(id="s2", heading="Weapons", heading_level=2, parent_section_id="s1", heading_path=["Combat", "Weapons"]),
    ]
    blocks = [
        SimpleNamespace(id="b1", section_id="s2", block_type="paragraph", reading_order=1,
                        provenance=[{"page_no": 12}], block_metadata={}, display_text="Weapons text"),
        SimpleNamespace(id="b2", section_id="s2", block_type="table", reading_order=2,
                        provenance=[{"page_no": 13}], block_metadata={}, display_text="Damage table"),
    ]
    pages = [SimpleNamespace(page_number=12, page_label="10"), SimpleNamespace(page_number=13, page_label="11")]
    chunks = [SimpleNamespace(id="c1", section_id="s2", chunk_role="child", ordinal=1)]
    first = build_book_map(document_id="doc", sections=sections, blocks=blocks, pages=pages, chunks=chunks)
    second = build_book_map(document_id="doc", sections=sections, blocks=blocks, pages=pages, chunks=chunks)
    assert first == second
    weapons = first["sections"][1]
    assert weapons["parent_id"] == "s1"
    assert weapons["pages"] == {"start": 12, "end": 13, "start_label": "10", "end_label": "11"}
    assert weapons["block_ids"] == ["b1", "b2"]
    assert weapons["chunk_ids"] == ["c1"]
    assert weapons["content_types"] == ["paragraph", "table"]
    assert weapons["node_type"] == "section"
    assert first["sections"][0]["node_type"] == "chapter"
    assert weapons["mapping_status"] == "tentative"
    assert weapons["source_block_ids"] == ["b1", "b2"]
    table = first["sections"][2]
    assert table["node_type"] == "table" and table["parent_id"] == "s2"
    assert table["mapping_status"] == "verified" and table["pages"]["start"] == 13
    assert first["quality"]["coverage"] == 1


def test_book_map_keeps_heading_without_evidence_explicitly_unresolved():
    result = build_book_map(document_id="doc", sections=[SimpleNamespace(
        id="orphan", heading=None, heading_level=1, parent_section_id="missing", heading_path=[]
    )], blocks=[], pages=[], chunks=[])
    node = result["sections"][0]
    assert node["title"] == "Unresolved section"
    assert node["mapping_status"] == "unresolved"
    assert node["confidence"] == 0
    assert node["parent_id"] is None


def test_book_map_links_images_to_text_captions_and_caption_chunks():
    section = SimpleNamespace(id="s1", heading="Combat", heading_level=1,
                              parent_section_id=None, heading_path=["Combat"])
    blocks = [
        SimpleNamespace(id="image", section_id="s1", block_type="picture", reading_order=4,
                        provenance=[{"page_no": 3}], display_text=""),
        SimpleNamespace(id="caption", section_id="s1", block_type="caption", reading_order=5,
                        provenance=[{"page_no": 3}], display_text="Figure 1: Attack sequence"),
    ]
    chunks = [SimpleNamespace(id="caption-chunk", section_id="s1", chunk_role="child", ordinal=1,
                              source_block_ids=["caption"])]
    result = build_book_map(document_id="doc", sections=[section], blocks=blocks,
                            pages=[SimpleNamespace(page_number=3, page_label="3")], chunks=chunks)
    image = result["sections"][1]
    assert image["node_type"] == "image"
    assert image["title"] == "Figure 1: Attack sequence"
    assert image["block_ids"] == ["image", "caption"]
    assert image["caption_block_ids"] == ["caption"]
    assert image["chunk_ids"] == ["caption-chunk"]
    assert image["pages"]["start"] == 3


def test_book_map_api_is_scoped_and_returns_active_generation(direct):
    client, headers, _ = direct
    item = add(client, headers).json()
    document_id = str(uuid4())
    payload = {"document_id": document_id, "map_version": 3, "sections": [], "unmapped_block_ids": [],
               "quality": {"section_count": 0, "block_count": 0, "mapped_block_count": 0,
                           "coverage": 1.0, "unmapped_block_count": 0, "uncertain_section_ids": []}}
    with session_factory()() as session:
        session.add(ShreckWorldDocument(id=document_id, library_item_id=item["id"], is_active=True,
            source_sha256=item["source_sha256"], parser_name="plain_text", parser_version="1",
            embedding_version="test", embedding_model="test", embedding_fingerprint="a" * 64,
            embedding_dimension=2, manifest_path="/tmp/manifest"))
        session.flush()
        session.add(ShreckWorldDocumentBookMap(document_id=document_id, map_version=1, payload=payload))
        session.commit()
    endpoint = f"/ontologies/7/items/{item['id']}/book-map"
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200 and response.json() == payload
    assert client.get(f"/ontologies/8/items/{item['id']}/book-map", headers=headers).status_code == 404


def test_existing_generation_backfill_is_idempotent_and_dry_run_safe(direct, monkeypatch):
    client, headers, _ = direct
    item = add(client, headers).json()
    document_id = str(uuid4())
    with session_factory()() as session:
        session.add(ShreckWorldDocument(id=document_id, library_item_id=item["id"], is_active=True,
            source_sha256=item["source_sha256"], parser_name="plain_text", parser_version="1",
            embedding_version="test", embedding_model="test", embedding_fingerprint="a" * 64,
            embedding_dimension=2, manifest_path="/tmp/manifest"))
        session.flush()
        session.add(ShreckWorldDocumentSection(id="section", document_id=document_id,
            parent_section_id=None, heading="Notes", heading_level=1, heading_path=["Notes"]))
        session.add(ShreckWorldDocumentChunk(id="legacy-chunk", library_item_id=item["id"],
            document_id=None, page_number=1, ordinal=0, text="Stored vector passage",
            embedding_json='[0.25, 0.75]', chunk_role="child", section_id="section",
            heading_path=["Notes"], source_block_ids=[], page_numbers=[1]))
        session.commit()
    from scripts import backfill_book_maps
    monkeypatch.setattr(backfill_book_maps, "create_schema", lambda: None)
    assert backfill_book_maps.backfill(batch_size=1, dry_run=True) == (1, 0, 0)
    with session_factory()() as session:
        assert session.get(ShreckWorldDocumentBookMap, document_id) is None
    assert backfill_book_maps.backfill(batch_size=1) == (1, 0, 0)
    assert backfill_book_maps.backfill(batch_size=1) == (0, 0, 0)
    with session_factory()() as session:
        record = session.get(ShreckWorldDocumentBookMap, document_id)
        record.map_version = 1
        record.payload["map_version"] = 1
        session.commit()
    from app.services.book_map import has_missing_book_maps
    with session_factory()() as session:
        assert has_missing_book_maps(session)
    assert backfill_book_maps.backfill(batch_size=1) == (1, 0, 0)
    with session_factory()() as session:
        record = session.get(ShreckWorldDocumentBookMap, document_id)
        chunk = session.get(ShreckWorldDocumentChunk, "legacy-chunk")
        assert record.map_version == record.payload["map_version"] == 3
        assert "legacy-chunk" in record.payload["sections"][0]["chunk_ids"]
        assert chunk.embedding_json == "[0.25, 0.75]" and chunk.document_id is None


def test_service_startup_enqueues_backfill_when_generations_are_missing_maps(api, monkeypatch):
    item = upload(api, params={"title": "Book"}).json()
    document_id = str(uuid4())
    with session_factory()() as session:
        session.add(ShreckWorldDocument(id=document_id, library_item_id=item["id"], is_active=True,
            source_sha256=item["source_sha256"], parser_name="plain_text", parser_version="1",
            embedding_version="test", embedding_model="test", embedding_fingerprint="a" * 64,
            embedding_dimension=2, manifest_path="/tmp/manifest"))
        session.commit()
    queued = []
    monkeypatch.setattr("app.tasks.book_map.backfill_book_maps_on_startup.apply_async", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr("app.main.get_embedder", lambda: SimpleNamespace(ensure_ready=lambda: {}))
    class InlineThread:
        def __init__(self, *, target, **_kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr("app.main.Thread", InlineThread)
    from app.main import startup
    startup()
    assert queued == [{"retry": False}]
