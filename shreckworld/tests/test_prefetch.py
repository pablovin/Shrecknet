from pathlib import Path

from huggingface_hub.errors import LocalEntryNotFoundError

from app import prefetch


def test_model_prefetch_uses_local_snapshot_without_network(monkeypatch, tmp_path):
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return "/models/cached"

    monkeypatch.setattr(prefetch, "snapshot_download", snapshot_download)
    result = prefetch._ensure_snapshot("org/model", "revision", tmp_path)

    assert result == "/models/cached"
    assert len(calls) == 1
    assert calls[0]["local_files_only"] is True
    assert calls[0]["cache_dir"] == str(tmp_path)


def test_model_prefetch_downloads_only_after_local_cache_miss(monkeypatch, tmp_path):
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise LocalEntryNotFoundError("not cached")
        return str(Path(tmp_path) / "downloaded")

    monkeypatch.setattr(prefetch, "snapshot_download", snapshot_download)
    result = prefetch._ensure_snapshot("org/model", None, tmp_path)

    assert result == str(tmp_path / "downloaded")
    assert [call.get("local_files_only", False) for call in calls] == [True, False]
    assert all(call["allow_patterns"] == calls[0]["allow_patterns"] for call in calls)
