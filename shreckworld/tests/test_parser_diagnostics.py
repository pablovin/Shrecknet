import logging

from app.services.document_parsing import ParserDiagnostics


def test_explicit_table_content_loss_is_reported_without_guessing_coverage():
    capture = ParserDiagnostics()
    record = logging.LogRecord("MatchingPostProcessor", logging.WARNING, "", 1,
        "309 of 567 pdf cells matched neither a row nor a column band of the 2x2 grid and were dropped from the table", (), None)
    capture.emit(record)
    capture.emit(record)
    assert capture.drop_reports == capture.records == 2
    assert capture.dropped_cells == 618 and capture.reported_cells == 1134
    # Report counts may overlap; the text is deduplicated, not the observed logs.
    assert len(capture.messages) == 1 and "dropped from the table" in capture.messages[0]
    capture.emit(logging.LogRecord("unrelated", logging.WARNING, "", 1, "provider unavailable", (), None))
    assert capture.records == 2


def test_diagnostics_are_bounded_and_docling_errors_are_preserved():
    capture = ParserDiagnostics()
    for index in range(50):
        capture.emit(logging.LogRecord("docling.parser", logging.ERROR, "", 1, f"error {index} " + "x" * 2000, (), None))
    assert capture.records == 50 and len(capture.messages) == 32
    assert all(len(message) <= 1007 for message in capture.messages)


def test_parse_source_retains_diagnostics_and_removes_capture_handler(tmp_path, monkeypatch):
    from app.services.document_parsing import parse_source
    from test_document_metadata import pdf_bytes
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes())
    class Adapter:
        def __init__(self, *args, **kwargs):
            assert kwargs["require_complete"]
        def parse(self, *args):
            logging.getLogger("MatchingPostProcessor").warning(
                "1 of 3 pdf cells matched neither a row nor a column band of the 2x2 grid and were dropped from the table")
            return None, {"pages": {"1": {}}}, "", [{"reference": "table", "block_type": "table",
                "text": "| Item | Value |", "reading_order": 1, "provenance": [{"page_no": 1}]}], []
    monkeypatch.setattr("app.services.document_parsing.DoclingAdapter", Adapter)
    monkeypatch.setattr("app.services.document_parsing.version", lambda name: "test")
    before = list(logging.getLogger().handlers)
    result = parse_source(source, ingestion_id="attempt", output_dir=tmp_path / "parsed", heartbeat=lambda count: None)
    assert logging.getLogger().handlers == before
    assert result.quality["table_cell_drop_reports"] == 1
    assert result.quality["reported_dropped_table_cells"] == 1
    assert result.quality["reported_table_cells"] == 3
    assert result.quality["explicit_parser_diagnostics"] == 1
    assert "dropped from the table" in result.diagnostics[0]
