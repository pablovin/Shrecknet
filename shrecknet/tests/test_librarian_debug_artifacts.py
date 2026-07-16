from __future__ import annotations

import json

from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts


def test_librarian_debug_artifacts_write_numbered_input_output_files(tmp_path) -> None:
    artifacts = LibrarianDebugArtifacts(tmp_path)

    first = artifacts.write(
        "chunks found",
        input={"query": "How does armor work?"},
        output={"chunks": [{"page_number": 42, "text": "Armor absorbs damage."}]},
    )
    second = artifacts.write(
        "llm context window",
        input={"chunks": 1},
        output={"messages": [{"role": "user", "content": "Answer this"}]},
    )

    assert first is not None
    assert second is not None
    assert (tmp_path / "01_chunks_found.json").exists()
    assert (tmp_path / "02_llm_context_window.json").exists()

    payload = json.loads((tmp_path / "01_chunks_found.json").read_text(encoding="utf-8"))
    assert payload["step"] == "chunks found"
    assert payload["input"]["query"] == "How does armor work?"
    assert payload["output"]["chunks"][0]["page_number"] == 42
    assert payload["created_at"]
