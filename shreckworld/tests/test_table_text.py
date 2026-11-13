from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage, NormalizedSection, build_chunks
from shreckdoc.structure_types import TableCell, TableStructure
from shreckdoc.table_text import bounded_table_row_groups, serialize_table_text


class _Tokenizer:
    def encode(self, text, add_special_tokens=True):
        return text.split()

    def decode(self, tokens, **kwargs):
        return " ".join(tokens)


def _table():
    return TableStructure(cells=[
        TableCell("Trait", 0, 0, is_header=True),
        TableCell("Dice", 0, 1, is_header=True),
        TableCell("Strength", 1, 0),
        TableCell("d6 + 2", 1, 1, header_path=["Dice", "Pool"]),
        TableCell("Dexterity", 2, 0),
        TableCell("d10", 2, 1, header_path=["Dice", "Pool"]),
    ], row_count=3, column_count=2)


def test_table_serializer_preserves_header_paths_values_and_missing_cells():
    result = serialize_table_text(_table(), "fallback")
    assert result.quality == "structured"
    assert "Dice / Pool" in result.text
    assert "d6 + 2" in result.text and "d10" in result.text
    assert "Strength" in result.text and "Dexterity" in result.text
    missing = TableStructure(cells=[TableCell("Header", 0, 0, is_header=True),
                                    TableCell("Value", 1, 0)], row_count=2, column_count=2)
    missing_text = serialize_table_text(missing).text
    assert "Column 2" not in missing_text


def test_text_fallback_and_unreadable_tables_keep_original_extracted_text():
    fallback = serialize_table_text(TableStructure(structure_quality="text_fallback"), "raw | table")
    unreadable = serialize_table_text(TableStructure(structure_quality="unreadable"), "retained source text")
    assert fallback.text == "raw | table" and fallback.quality == "text_fallback"
    assert unreadable.text == "retained source text" and unreadable.quality == "unreadable"


def test_bounded_groups_repeat_headers_and_keep_oversized_rows_for_token_window_fallback():
    serialized = serialize_table_text(_table())
    groups = bounded_table_row_groups(serialized, count_tokens=lambda value: len(value.split()), max_tokens=14)
    assert len(groups) == 2
    assert all(group.count("| Header | Value |") == 1 for group in groups)
    assert all("| --- | --- |" in group for group in groups)
    large = TableStructure(cells=[TableCell("H", 0, 0, is_header=True),
                                  TableCell("x " * 50, 1, 0)], row_count=2, column_count=1)
    oversized = bounded_table_row_groups(serialize_table_text(large),
                                         count_tokens=lambda value: len(value.split()), max_tokens=10)
    assert len(oversized) == 1 and len(oversized[0].split()) > 10


def test_build_chunks_uses_serialized_table_text_and_keeps_source_block_identity():
    page = NormalizedPage("p1", 1, "1")
    section = NormalizedSection("s1", None, "Rules", 1, ["Rules"], ["table"], [1], ["1"])
    block = NormalizedBlock("table", "s1", "table", "raw table text", [1], ["1"], [], 0,
                            table_structure=_table())
    document = NormalizedDocument([page], [section], [block])
    chunks = build_chunks(document=document, ingestion_id="job", library_item_id="item", ontology_id=1,
                          book_title="Rules", rpg_system="", tokenizer=_Tokenizer(), max_embedding_tokens=100,
                          preserve_unit_boundaries=True,
                          table_text_by_block={"table": serialize_table_text(_table()).text})
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
    assert children and "d6 + 2" in children[0].display_text
    assert children[0].source_block_ids == ["table"]


def test_build_chunks_repeats_headers_and_keeps_cell_groups_together():
    table = TableStructure(cells=[TableCell("Trait", 0, 0, is_header=True),
        TableCell("Dice", 0, 1, is_header=True),
        *[cell for index in range(1, 7) for cell in (
            TableCell(f"Trait{index}", index, 0),
            TableCell(f"d{index + 4}", index, 1, header_path=["Dice", "Pool"]) )]],
        row_count=7, column_count=2)
    serialized = serialize_table_text(table)
    page = NormalizedPage("p1", 1, "1")
    section = NormalizedSection("s1", None, "Rules", 1, ["Rules"], ["table"], [1], ["1"])
    block = NormalizedBlock("table", "s1", "table", "raw", [1], ["1"], [], 0,
                            table_structure=table)
    chunks = build_chunks(document=NormalizedDocument([page], [section], [block]), ingestion_id="job",
        library_item_id="item", ontology_id=1, book_title="R", rpg_system="", tokenizer=_Tokenizer(),
        max_embedding_tokens=45, preserve_unit_boundaries=True,
        table_text_by_block={"table": serialized.text},
        table_row_groups_by_block={"table": serialized.row_groups})
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
    assert len(children) >= 2
    assert all("| Header | Value |" in chunk.display_text for chunk in children)
    assert all(("Strength" in chunk.display_text) == ("d6 + 2" in chunk.display_text)
               for chunk in children)


def test_long_heading_path_is_reduced_to_nearest_supported_context_without_dropping_text():
    page = NormalizedPage("p1", 1, "1")
    path = [f"Deep parent {index}" for index in range(20)]
    section = NormalizedSection("s1", None, path[-1], len(path), path, ["body"], [1], ["1"])
    block = NormalizedBlock("body", "s1", "paragraph", "Actual source text stays embedded.", [1], ["1"], [], 1)
    chunks = build_chunks(document=NormalizedDocument([page], [section], [block]),
                          ingestion_id="job", library_item_id="item", ontology_id=1,
                          book_title="Book", rpg_system="System", tokenizer=_Tokenizer(),
                          max_embedding_tokens=40, preserve_unit_boundaries=True)
    child = next(chunk for chunk in chunks if chunk.chunk_role == "child")
    assert child.context_reduced
    assert child.heading_path[-1] == path[-1]
    assert child.display_text == "Actual source text stays embedded."
    assert len(_Tokenizer().encode(child.embedding_text)) <= 40
