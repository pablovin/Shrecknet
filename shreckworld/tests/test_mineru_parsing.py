import io
import json
import zipfile

import httpx
import pymupdf

from app.core.config import Settings
from app.services import mineru_parsing


def _archive(payload):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("sample/structured_content.json", json.dumps(payload))
    return output.getvalue()


def _pdf(path):
    document = pymupdf.open()
    document.new_page(width=600, height=800)
    document.save(path)
    document.close()


def test_mineru_structured_content_becomes_ordered_heading_and_page_provenance(tmp_path):
    path = tmp_path / "source.pdf"
    _pdf(path)
    canonical, items = mineru_parsing._normalize_result({
        "metadata": {"producer": {"version": "4.1.7"}},
        "pages": [{"page_idx": 0, "blocks": [
            {"index": 0, "type": "paragraph_title", "content": {"level": 1, "content": [{"type": "text", "content": "Chapter I"}]}, "bbox": [100, 100, 900, 160]},
            {"index": 1, "type": "text", "content": "The rules begin.", "bbox": [100, 170, 900, 220]},
        ]}],
    }, path)
    assert canonical["pages"]["1"]["size"] == {"width": 600.0, "height": 800.0}
    assert [(item["block_type"], item.get("level"), item["text"]) for item in items] == [
        ("heading", 1, "Chapter I"), ("paragraph", None, "The rules begin."),
    ]
    assert items[0]["provenance"][0]["bbox"] == {
        "left": 60.0, "top": 80.0, "right": 540.0, "bottom": 128.0, "coord_origin": "TOPLEFT",
    }


def test_mineru_preserves_textless_blocks_unknown_types_and_separate_captions(tmp_path):
    path = tmp_path / "source.pdf"
    _pdf(path)
    canonical, items = mineru_parsing._normalize_result({"pages": [{"page_idx": 0, "blocks": [
        {"id": "table-1", "index": 0, "type": "table", "content": {"table_body": [["A", "B"]], "table_caption": ["Table one"]}},
        {"id": "image-1", "index": 1, "type": "image", "content": "", "bbox": [10, 20, 30, 40]},
        {"id": "future-1", "index": 2, "type": "new_layout_kind", "content": ""},
        {"id": "bad-box", "index": 3, "type": "text", "content": "text", "bbox": [10, 20, 40]},
    ]}]}, path)
    assert [item["block_type"] for item in items] == ["table", "caption", "picture", "unknown", "paragraph"]
    assert items[0]["text"] == ""
    assert items[0]["native_metadata"]["source_content"]["table_body"] == [["A", "B"]]
    assert items[1]["text"] == "Table one"
    assert items[1]["native_metadata"]["parent_reference"] == items[0]["reference"]
    assert items[2]["text"] == "" and items[2]["provenance"][0]["bbox"]["left"] == 6
    assert items[3]["native_type"] == "new_layout_kind" and items[3]["text"] == ""
    assert canonical["diagnostics"] == ["MinerU page 1 block 3 has invalid or ambiguous bbox; omitted coordinates"]


def test_mineru_tasks_api_uploads_polls_and_normalizes_result(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    _pdf(source)
    archive = _archive({
        "metadata": {"producer": {"version": "4.1.7"}},
        "pages": [{"page_idx": 0, "blocks": [
            {"index": 0, "type": "paragraph_title", "content": {"level": 1, "content": "Chapter I"}},
        ]}],
    })
    uploaded = []

    def respond(request):
        if request.method == "POST" and request.url.path == "/v1/tasks":
            return httpx.Response(201, json={"task_id": "task-1", "files": [{
                "file_id": "file-1", "upload": {"url": "http://mineru:8000/upload/file-1", "headers": {"Content-Type": "application/pdf"}},
            }]})
        if request.method == "PUT" and request.url.path == "/upload/file-1":
            uploaded.append(request.content)
            return httpx.Response(200)
        if request.method == "GET" and request.url.path == "/v1/tasks/task-1":
            return httpx.Response(200, json={"task_id": "task-1", "files": [{
                "file_id": "file-1", "state": "completed", "result_url": "http://mineru:8000/results/file-1",
            }]})
        if request.method == "GET" and request.url.path == "/results/file-1":
            return httpx.Response(200, content=archive)
        raise AssertionError(f"Unexpected MinerU request {request.method} {request.url}")

    monkeypatch.setattr(mineru_parsing, "get_settings", lambda: Settings(mineru_api_url="http://mineru:8000", mineru_poll_interval_seconds=0.25))
    heartbeats = []
    canonical, items, version = mineru_parsing.parse_pdf(
        source, output_dir=tmp_path / "parsed", heartbeat=lambda: heartbeats.append(True),
        transport=httpx.MockTransport(respond),
    )
    assert uploaded and uploaded[0].startswith(b"%PDF")
    assert canonical["pages"].keys() == {"1"}
    assert items[0]["text"] == "Chapter I"
    assert version == "4.1.7"
    assert heartbeats
