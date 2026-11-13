from pathlib import Path
from types import SimpleNamespace

from shreckdoc.pipeline import DoclingAdapter, normalize_document


def _prov(page=2, order=0):
    return SimpleNamespace(page_no=page, charspan=(order, order + 4), bbox=SimpleNamespace(
        l=10.0, t=20.0, r=50.0, b=60.0, coord_origin=SimpleNamespace(value="TOPLEFT"),
    ))


def test_docling_adapter_preserves_heading_table_picture_list_and_unknown_evidence():
    cells = [
        SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0, row_span=1, col_span=2,
                        text="Outcome", column_header=True, row_header=False, row_section=False,
                        bbox=SimpleNamespace(l=10, t=20, r=90, b=30, coord_origin="TOPLEFT")),
        SimpleNamespace(start_row_offset_idx=1, start_col_offset_idx=0, row_span=1, col_span=1,
                        text="Success", column_header=False, row_header=True, row_section=False,
                        bbox=None),
        SimpleNamespace(start_row_offset_idx=1, start_col_offset_idx=1, row_span=1, col_span=1,
                        text="The party escapes.", column_header=False, row_header=False,
                        row_section=False, bbox=None),
    ]
    table = SimpleNamespace(
        label="table", self_ref="#/tables/0", text="", level=0, prov=[_prov(2, 5)],
        parent=SimpleNamespace(cref="#/groups/1"),
        data=SimpleNamespace(table_cells=cells, num_rows=2, num_cols=2,
                             orientation=SimpleNamespace(value="rot_0")),
        export_to_markdown=lambda **_: "| Outcome | Result |\n| --- | --- |\n| Success | The party escapes. |",
        captions=[SimpleNamespace(cref="#/texts/7")],
    )
    picture = SimpleNamespace(label="picture", self_ref="#/pictures/0", text="", level=0,
                              prov=[_prov(3, 8)], parent=None, image=object(), captions=[])
    items = [
        (SimpleNamespace(label="section_header", self_ref="#/texts/0", text="Resolution",
                         level=1, prov=[_prov(2, 1)], parent=None, orig="Resolution",
                         formatting={"bold": True, "font_size": 18}), 1),
        (SimpleNamespace(label="list_item", self_ref="#/texts/1", text="Roll one die.",
                         level=0, prov=[_prov(2, 2)], parent=None,
                         enumerated=True, marker="1."), 0),
        (table, 0),
        (picture, 0),
        (SimpleNamespace(label="unrecognized_custom_label", self_ref="#/texts/8",
                         text="Retain this text.", level=0, prov=[_prov(3, 9)], parent=None), 0),
    ]

    class Document:
        def iterate_items(self, with_groups=False):
            assert with_groups
            return iter(items)

    primitives = DoclingAdapter()._items_to_primitives(Document())
    assert [item["block_type"] for item in primitives] == ["heading", "list_item", "table", "picture", "unknown"]
    assert primitives[0]["native_metadata"]["orig"] == "Resolution"
    assert primitives[0]["native_metadata"]["formatting"] == {"bold": True, "font_size": 18}
    assert primitives[1]["native_metadata"]["enumerated"] is True
    assert primitives[1]["native_metadata"]["marker"] == "1."
    assert primitives[2]["native_metadata"]["caption_references"] == ["#/texts/7"]
    assert primitives[3]["text"] == "" and primitives[3]["native_metadata"]["picture_data_available"] is True
    assert primitives[4]["text"] == "Retain this text."

    document = normalize_document(
        ingestion_id="synthetic",
        pdf_path=Path("synthetic.pdf"),
        canonical={"pages": {"2": {}, "3": {}}},
        items=primitives,
        page_labeler=lambda _path, count: {page: str(page) for page in range(1, count + 1)},
    )
    table_block = next(block for block in document.blocks if block.block_type == "table")
    assert table_block.table_structure is not None
    assert table_block.table_structure.row_count == 2 and table_block.table_structure.column_count == 2
    assert [(cell.row, cell.column, cell.row_span, cell.column_span, cell.is_header, cell.is_row_header)
            for cell in table_block.table_structure.cells] == [
                (0, 0, 1, 2, True, False), (1, 0, 1, 1, False, True), (1, 1, 1, 1, False, False),
            ]
    image_block = next(block for block in document.blocks if block.block_type == "picture")
    assert image_block.display_text == "" and image_block.native_type == "picture"
    assert image_block.page_numbers == [3]


def test_docling_missing_optional_native_capabilities_still_keeps_item():
    item = SimpleNamespace(label="table", self_ref="#/tables/empty", text="", level=0,
                           prov=[], parent=None)

    class Document:
        def iterate_items(self, with_groups=False):
            return iter([(item, 0)])

    primitive = DoclingAdapter()._items_to_primitives(Document())[0]
    assert primitive["block_type"] == "table"
    assert primitive["text"] == ""
    assert primitive["native_metadata"] == {}
