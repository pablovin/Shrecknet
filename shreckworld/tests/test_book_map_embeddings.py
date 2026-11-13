from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage, NormalizedSection, build_chunks

from semantic_helpers import WordTokenizer


def test_index_chunks_embed_all_extracted_text_with_hierarchy_and_caption_but_not_image_pixels():
    section = NormalizedSection(
        section_id="section-characteristics", parent_section_id=None,
        heading="Characteristics", heading_level=2,
        heading_path=["Character Creation", "Characteristics"],
    )
    contents = [
        ("heading", "Characteristics", 0),
        ("page_header", "Core Rules running header", 1),
        ("paragraph", "Roll three dice and add them together.", 2),
        ("table", "Characteristic | Range\nStrength | 3–18", 3),
        ("picture", "", 4),
        ("caption", "Figure 1: The characteristic roll", 5),
        ("page_footer", "Publisher page footer", 6),
    ]
    blocks = [NormalizedBlock(
        block_id=f"block-{index}", section_id=section.section_id,
        block_type=kind, display_text=text, page_numbers=[34], page_labels=["22"],
        reading_order=order, metadata={},
    ) for index, (kind, text, order) in enumerate(contents)]
    section.block_ids = [block.block_id for block in blocks]
    document = NormalizedDocument(
        pages=[NormalizedPage("page-34", 34, "22", 600, 800)],
        sections=[section], blocks=blocks,
    )

    chunks = build_chunks(
        document=document, ingestion_id="generation", library_item_id="book",
        ontology_id="7", book_title="Core Rules", rpg_system="RuneQuest",
        tokenizer=WordTokenizer(), max_embedding_tokens=100, preserve_unit_boundaries=True,
    )
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
    by_source_block = {block_id: chunk for chunk in children for block_id in chunk.source_block_ids}

    for index, (kind, text, _) in enumerate(contents):
        block_id = f"block-{index}"
        if kind == "picture" and not text:
            assert block_id not in by_source_block
            continue
        if kind == "heading":
            # Heading text is carried structurally on every contextual chunk.
            assert all("Characteristics" in chunk.embedding_text for chunk in children)
            continue
        assert block_id in by_source_block, kind
        chunk = by_source_block[block_id]
        assert text in chunk.display_text
        assert "Character Creation > Characteristics" in chunk.embedding_text
        assert "passage: Book: Core Rules" in chunk.embedding_text

    table_chunk = by_source_block["block-3"]
    caption_chunk = by_source_block["block-5"]
    assert table_chunk.content_type == "table"
    assert caption_chunk.display_text == "Figure 1: The characteristic roll"


def test_picture_text_and_unreliable_table_text_are_retained_without_embedding():
    section = NormalizedSection("s", None, "Chapter", 1, ["Chapter"])
    blocks = [NormalizedBlock(f"b{i}", "s", kind, text, [1], ["1"], [], i, f"r{i}")
              for i, (kind, text) in enumerate([
                  ("paragraph", "Readable prose"),
                  ("picture", "Parser supplied image label"),
                  ("table", "Unreadable cell dump"),
                  ("unparsed_region", "Unparsed placeholder"),
              ])]
    section.block_ids = [block.block_id for block in blocks]
    document = NormalizedDocument([NormalizedPage("p1", 1, "1")], [section], blocks)
    chunks = build_chunks(document=document, ingestion_id="g", library_item_id="b",
        ontology_id="o", book_title="Book", rpg_system="Game", tokenizer=WordTokenizer(),
        preserve_unit_boundaries=True, table_text_by_block={"b2": ""})
    embedded = {block_id for chunk in chunks if chunk.chunk_role == "child"
                for block_id in chunk.source_block_ids}
    assert embedded == {"b0"}
    assert {block.block_id for block in document.blocks} == {"b0", "b1", "b2", "b3"}
