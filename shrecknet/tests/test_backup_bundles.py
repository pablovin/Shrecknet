import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.services.backup.categories import (
    BackupContext,
    BundleCategory,
    FileCategory,
    FileSource,
    ShreckRpgCategory,
    names,
)
from app.services.backup.rpg_prune import RpgPruneError, prune_to_user
from app.services.backup.snapshots import SnapshotService

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE games (id INTEGER PRIMARY KEY, owner_user_id INTEGER, name TEXT);
CREATE TABLE game_memberships (id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id) ON DELETE CASCADE, user_id INTEGER);
CREATE TABLE chronicles (id TEXT PRIMARY KEY, game_id INTEGER REFERENCES games(id) ON DELETE CASCADE);
CREATE TABLE notes (id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id) ON DELETE SET NULL, owner_user_id INTEGER);
CREATE TABLE note_shares (id INTEGER PRIMARY KEY, note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE, user_id INTEGER);
CREATE TABLE chronicle_assets (id TEXT PRIMARY KEY, chronicle_id INTEGER REFERENCES notes(id) ON DELETE CASCADE, extension TEXT);
CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT);
CREATE TABLE page_user_visits (id INTEGER PRIMARY KEY, page_key TEXT, user_id TEXT, visit_count INTEGER);
CREATE TABLE page_visit_stats (page_key TEXT PRIMARY KEY, total_visits INTEGER, unique_users INTEGER);
CREATE TABLE shreckcompanion_webhook_outbox (event_id TEXT PRIMARY KEY, payload_json TEXT);
"""


def make_rpg(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "shreckrpg.db")
    db.executescript(SCHEMA)
    db.executescript(
        """
        INSERT INTO games VALUES (1, 7, 'mine'), (2, 9, 'theirs');
        INSERT INTO game_memberships VALUES (1, 1, 7), (2, 1, 9), (3, 2, 9), (4, 2, 7);
        INSERT INTO chronicles VALUES ('c1', 1), ('c2', 2);
        INSERT INTO notes VALUES (1, 1, 7), (2, 2, 9), (3, NULL, 9);
        INSERT INTO note_shares VALUES (1, 1, 9), (2, 1, 7), (3, 2, 7);
        INSERT INTO chronicle_assets VALUES ('aaa', 1, 'png'), ('bbb', 2, 'png');
        INSERT INTO notifications VALUES ('n1', '7'), ('n2', '9');
        INSERT INTO page_user_visits VALUES (1, 'home', '7', 2), (2, 'home', '9', 5), (3, 'other', '9', 1);
        INSERT INTO page_visit_stats VALUES ('home', 7, 2), ('other', 1, 1);
        INSERT INTO shreckcompanion_webhook_outbox VALUES ('e1', '{"user": 9}');
        """
    )
    db.commit()
    db.close()
    for chronicle, asset in (("1", "aaa.png"), ("2", "bbb.png")):
        (root / "chronicle-assets" / chronicle).mkdir(parents=True, exist_ok=True)
        (root / "chronicle-assets" / chronicle / asset).write_bytes(b"png")
    return root


def rows(root: Path, query: str) -> list:
    db = sqlite3.connect(root / "shreckrpg.db")
    try:
        return db.execute(query).fetchall()
    finally:
        db.close()


def test_prune_keeps_only_the_selected_user(tmp_path: Path) -> None:
    root = make_rpg(tmp_path / "rpg")
    report = prune_to_user(root, 7)

    assert rows(root, "SELECT id FROM games") == [(1,)]
    assert rows(root, "SELECT user_id FROM game_memberships") == [(7,)]
    assert rows(root, "SELECT id FROM chronicles") == [("c1",)]
    assert rows(root, "SELECT id FROM notes") == [(1,)]
    assert rows(root, "SELECT user_id FROM note_shares") == [(7,)]
    assert rows(root, "SELECT id FROM notifications") == [("n1",)]
    assert rows(root, "SELECT * FROM shreckcompanion_webhook_outbox") == []
    assert rows(root, "SELECT page_key, total_visits, unique_users FROM page_visit_stats") == [("home", 2, 1)]
    assert [p.name for p in (root / "chronicle-assets").rglob("*.png")] == ["aaa.png"]
    assert report.deleted_asset_files == 1
    assert report.to_dict()["deleted_rows"]["games"] == 1


def test_prune_refuses_tables_without_a_rule(tmp_path: Path) -> None:
    root = make_rpg(tmp_path / "rpg")
    db = sqlite3.connect(root / "shreckrpg.db")
    db.execute("CREATE TABLE future_feature (id INTEGER, user_id INTEGER)")
    db.commit()
    db.close()
    with pytest.raises(RpgPruneError, match="future_feature"):
        prune_to_user(root, 7)
    assert len(rows(root, "SELECT id FROM games")) == 2


def _rpg_category(root: Path) -> ShreckRpgCategory:
    return ShreckRpgCategory(
        id="shreckrpg_files", label="RPG", description="", owner="shreckrpg",
        sources=lambda: [FileSource("datasets", root, lambda e: e.name != "backups")],
    )


@pytest.mark.asyncio
async def test_rpg_restore_prunes_the_staged_copy_not_the_snapshot(tmp_path: Path) -> None:
    live = make_rpg(tmp_path / "live")
    category = _rpg_category(live)
    export = tmp_path / "snap" / "shreckrpg_files"
    await category.export(export, BackupContext())

    ctx = BackupContext(options={"rpg_keep_user_id": 7})
    result = await category.restore(export, ctx)

    assert rows(live, "SELECT id FROM games") == [(1,)]
    assert result["pruned_to_user"]["kept_user_id"] == "7"
    assert not [p for p in live.iterdir() if p.name.startswith(".rpg-restore")]
    assert len(rows(export / "datasets", "SELECT id FROM games")) == 2


@pytest.mark.asyncio
async def test_rpg_restore_without_option_restores_everything(tmp_path: Path) -> None:
    live = make_rpg(tmp_path / "live")
    category = _rpg_category(live)
    export = tmp_path / "snap" / "shreckrpg_files"
    await category.export(export, BackupContext())
    prune_to_user(live, 7)
    await category.restore(export, BackupContext())
    assert len(rows(live, "SELECT id FROM games")) == 2


def _bundle(tmp_path: Path) -> tuple[BundleCategory, Path, Path]:
    data, media = tmp_path / "data", tmp_path / "media"
    (data).mkdir()
    (media / "library").mkdir(parents=True)
    (media / "avatars").mkdir()
    (data / "main.db").write_bytes(b"")
    sqlite3.connect(data / "main.db").executescript("CREATE TABLE t(v); INSERT INTO t VALUES ('v1');")
    (media / "library" / "a.pdf").write_bytes(b"pdf1")
    (media / "avatars" / "a.png").write_bytes(b"png1")
    parts = [
        FileCategory(id="db", label="db", description="", owner="x",
                     sources=lambda: [FileSource("databases", data, lambda e: e.suffix == ".db")]),
        FileCategory(id="docs", label="docs", description="", owner="x",
                     sources=lambda: [FileSource("media", media, names("library"))]),
        FileCategory(id="imgs", label="imgs", description="", owner="x",
                     sources=lambda: [FileSource("media", media, names("avatars"))]),
    ]
    bundle = BundleCategory(id="worlds", label="Worlds", description="", owner="x", parts=parts)
    return bundle, data, media


@pytest.mark.asyncio
async def test_bundle_restores_all_parts_together_and_is_incremental(tmp_path: Path) -> None:
    bundle, data, media = _bundle(tmp_path)
    service = SnapshotService(root=tmp_path / "backups", categories={"worlds": bundle})
    first = await service.create_snapshot(["worlds"], BackupContext())
    second = await service.create_snapshot(["worlds"], BackupContext())
    assert first["categories"]["worlds"]["file_count"] == 3

    first_pdf = tmp_path / "backups" / first["id"] / "worlds" / "docs" / "media" / "library" / "a.pdf"
    second_pdf = tmp_path / "backups" / second["id"] / "worlds" / "docs" / "media" / "library" / "a.pdf"
    assert first_pdf.stat().st_ino == second_pdf.stat().st_ino

    (media / "library" / "a.pdf").write_bytes(b"changed")
    (media / "avatars" / "a.png").unlink()
    sqlite3.connect(data / "main.db").executescript("UPDATE t SET v='v2';")
    await service.restore_snapshot(second["id"], ["worlds"], BackupContext(), safety_snapshot=False)

    assert (media / "library" / "a.pdf").read_bytes() == b"pdf1"
    assert (media / "avatars" / "a.png").read_bytes() == b"png1"
    assert sqlite3.connect(data / "main.db").execute("SELECT v FROM t").fetchall() == [("v1",)]


@pytest.mark.asyncio
async def test_bundle_restore_rejects_a_snapshot_missing_a_part(tmp_path: Path) -> None:
    bundle, _data, _media = _bundle(tmp_path)
    service = SnapshotService(root=tmp_path / "backups", categories={"worlds": bundle})
    snap = await service.create_snapshot(["worlds"], BackupContext())
    import shutil
    shutil.rmtree(tmp_path / "backups" / snap["id"] / "worlds" / "imgs")
    with pytest.raises(Exception):
        await bundle.restore(tmp_path / "backups" / snap["id"] / "worlds", BackupContext())


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row

        return gen()


class _FakeNeo4jSession:
    def __init__(self, nodes, rels):
        self.nodes, self.rels = nodes, rels

    async def run(self, query, *args, **kwargs):
        if "SHOW" in query:
            return _FakeResult([])
        return _FakeResult(self.rels if "-[r]->" in query else self.nodes)


def test_graph_export_streams_legacy_layout_and_reports_progress(tmp_path):
    import json

    from app.services.backup.categories import Neo4jGraphCategory

    nodes = [{"id": f"n{i}", "labels": ["Entity"], "properties": {"name": f"e{i}"}} for i in range(5001)]
    rels = [{"id": "r1", "start_node_id": "n0", "end_node_id": "n1", "type": "KNOWS", "properties": {}}]
    notes: list[str] = []

    async def note(text, fraction=None):
        notes.append(text)

    ctx = BackupContext(neo4j_session=_FakeNeo4jSession(nodes, rels), note=note)
    result = asyncio.run(Neo4jGraphCategory().export(tmp_path / "graph", ctx))

    dump = json.loads((tmp_path / "graph" / "graph.json").read_text())
    assert len(dump["nodes"]) == 5001 and dump["relationships"][0]["type"] == "KNOWS"
    assert result.details == {"nodes": 5001, "relationships": 1}
    assert any("5,000" in text and "nodes" in text for text in notes)
