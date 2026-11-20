"""Import ShreckRPG games and sessions into canonical ShreckTables.

Dry-run by default, idempotent and non-destructive. Keep ShreckRPG DB available
for legacy Notes/Activities references until their consumers have migrated.

Usage:
 python -m scripts.migrate_shrecktables --rpg-db /path/shreckrpg.db \
   --shrecknet-db /path/shrecknet.db --target-db /path/shreckworld.db
 python -m scripts.migrate_shrecktables ... --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db.base import Base
import app.models  # noqa: F401 — register SQLAlchemy tables
from app.models.shrecktables import (
    Table, TableMember, TableSession, TablePoll, TablePollOption,
    TablePollVote, TableAttendance, LegacyTableMap,
)


def connect_readonly(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection, name):
    if not connection:
        return []
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
        return []
    return [dict(r) for r in connection.execute('SELECT * FROM "' + name + '"')]


def date(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Importer:
    def __init__(self, db, world_lookup, apply):
        self.db = db
        self.world_lookup = world_lookup
        self.apply = apply
        self.counts = Counter()
        self.warnings = []

    def lookup(self, source, old_id, cls):
        maprow = self.db.scalar(select(LegacyTableMap).where(
            LegacyTableMap.source == source, LegacyTableMap.old_id == str(old_id)))
        if maprow:
            result = self.db.get(cls, int(maprow.new_id))
            if not result:
                raise RuntimeError("Dangling legacy mapping: " + source + "/" + str(old_id))
            return result
        return None

    def mark(self, source, old_id, instance):
        self.db.add(instance)
        self.db.flush()
        self.db.add(LegacyTableMap(source=source, old_id=str(old_id), new_id=str(instance.id)))
        self.counts[source] += 1
        return instance

    def world(self, ontology_id):
        value = self.world_lookup.get(str(ontology_id))
        if not value:
            self.warnings.append("No canonical world mapping for ontology " + str(ontology_id))
        return value

    def import_rpg(self, rpg):
        games = rows(rpg, "games")
        memberships = rows(rpg, "game_memberships")
        sessions = rows(rpg, "game_sessions")
        attendance = rows(rpg, "game_session_attendance")
        polls = rows(rpg, "game_session_polls")
        options = rows(rpg, "game_session_poll_options")
        votes = rows(rpg, "game_session_poll_votes")
        game_map = {}
        for game in games:
            legacy = game["id"]
            world_id = self.world(game["ontology_id"])
            if not world_id:
                self.counts["skipped_games"] += 1
                continue
            table = self.lookup("rpg_game", legacy, Table)
            if not table:
                table = self.mark("rpg_game", legacy, Table(
                    ontology_id=game["ontology_id"], world_id=world_id,
                    name=game["name"], created_by=str(game["owner_user_id"]),
                    calendar_id=game.get("google_calendar_id"), vtt_url=game.get("vtt")))
            game_map[legacy] = table.id
            participants = {int(game["owner_user_id"]): "gm"}
            for member in memberships:
                if member["game_id"] == legacy and int(member["user_id"]) not in participants:
                    participants[int(member["user_id"])] = "gm" if member.get("role") in {"gm", "owner"} else "player"
            for user_id, role in participants.items():
                if not self.db.scalar(select(TableMember.id).where(
                    TableMember.table_id == table.id, TableMember.user_id == str(user_id))):
                    self.db.add(TableMember(table_id=table.id, user_id=str(user_id), role=role))
                    self.counts["members"] += 1
        self.db.flush()
        session_map = {}
        for source in sessions:
            table_id = game_map.get(source["game_id"])
            if table_id is None:
                self.counts["skipped_sessions"] += 1
                continue
            row = self.lookup("rpg_session", source["id"], TableSession)
            if not row:
                start = date(source.get("scheduled_date"))
                row = self.mark("rpg_session", source["id"], TableSession(
                    table_id=table_id, title=source["title"],
                    summary=source.get("summary") or "", location=source.get("location"),
                    scheduled_at_utc=start, scheduled_timezone=source.get("scheduled_timezone") or "UTC",
                    status="scheduled" if start else "draft",
                    google_event_id=source.get("google_event_id"),
                    google_meet_url=source.get("google_meet_link"),
                    calendar_sync_status="legacy_external" if source.get("google_event_id") else "not_configured",
                    created_by=str(next(g["owner_user_id"] for g in games if g["id"] == source["game_id"]))))
            session_map[source["id"]] = row.id
        self.db.flush()
        for item in attendance:
            sid = session_map.get(item["session_id"])
            if sid is None:
                continue
            if not self.db.scalar(select(TableAttendance.id).where(
                TableAttendance.session_id == sid, TableAttendance.user_id == str(item["user_id"]))):
                self.db.add(TableAttendance(session_id=sid, user_id=str(item["user_id"]),
                    attending=bool(item["attending"]), responded_at=date(item.get("responded_at"))))
                self.counts["attendance"] += 1
        poll_map = {}
        for item in polls:
            sid = session_map.get(item["session_id"])
            if sid is None:
                continue
            poll = self.lookup("rpg_poll", item["id"], TablePoll)
            if not poll:
                poll = self.mark("rpg_poll", item["id"], TablePoll(
                    session_id=sid, is_finalized=bool(item.get("is_finalized"))))
            poll_map[item["id"]] = poll.id
        option_map = {}
        for item in options:
            pid = poll_map.get(item["poll_id"])
            if pid is None:
                continue
            option = self.lookup("rpg_option", item["id"], TablePollOption)
            if not option:
                option = self.mark("rpg_option", item["id"], TablePollOption(
                    poll_id=pid, proposed_at_utc=date(item["proposed_start"])))
            option_map[item["id"]] = option.id
        self.db.flush()
        for item in votes:
            oid = option_map.get(item["option_id"])
            if oid is None:
                continue
            if not self.lookup("rpg_vote", item["id"], TablePollVote):
                self.mark("rpg_vote", item["id"], TablePollVote(
                    option_id=oid, user_id=str(item["user_id"])))
        for item in polls:
            if not item.get("finalized_option_id"):
                continue
            pid = poll_map.get(item["id"])
            option_id = option_map.get(item["finalized_option_id"])
            if pid is not None and option_id is not None:
                self.db.get(TablePoll, pid).finalized_option_id = option_id

    def import_engine(self):
        tables = rows(self.db.connection().connection, "shreck_tables")
        participants = rows(self.db.connection().connection, "shreck_table_participants")
        for source in tables:
            world_id = self.world(source["ontology_id"])
            if not world_id:
                self.counts["skipped_engine_tables"] += 1
                continue
            table = self.lookup("engine_table", source["id"], Table)
            if not table:
                table = self.mark("engine_table", source["id"], Table(
                    world_id=world_id, ontology_id=source["ontology_id"], name=source["name"],
                    version_id=source.get("version_id"), created_by=source["created_by"],
                    status=source.get("status") or "active"))
            members = [m for m in participants if m["table_id"] == source["id"]]
            if not any(m["user_id"] == source["created_by"] for m in members):
                members.append({"user_id": source["created_by"], "role": "gm"})
            for member in members:
                role = "gm" if member["role"] in {"owner", "gm"} else "player"
                if not self.db.scalar(select(TableMember.id).where(
                    TableMember.table_id == table.id, TableMember.user_id == str(member["user_id"]))):
                    self.db.add(TableMember(table_id=table.id, user_id=str(member["user_id"]),
                                            role=role, character_agent_id=member.get("character_agent_id")))
                    self.counts["members"] += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpg-db", type=Path, required=True)
    parser.add_argument("--shrecknet-db", type=Path, required=True)
    parser.add_argument("--target-db", type=Path, required=True)
    parser.add_argument("--world-map", type=Path, help="JSON mapping ontology IDs to canonical Shrecknet world IDs")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rpg = connect_readonly(args.rpg_db)
    shrecknet = connect_readonly(args.shrecknet_db)
    try:
        world_lookup = {str(row["id"]): str(row["world_id"]) for row in rows(shrecknet, "ontologies")
                        if row.get("world_id")}
        if args.world_map:
            world_lookup.update(json.loads(args.world_map.read_text()))
        if not args.target_db.exists():
            raise FileNotFoundError("Target database not found. Initialize ShreckWorld first.")
        engine = create_engine("sqlite:///" + str(args.target_db.resolve()),
                               connect_args={"check_same_thread": False})
        with Session(engine, autoflush=False) as db:
            try:
                importer = Importer(db, world_lookup, args.apply)
                importer.import_rpg(rpg)
                importer.import_engine()
                db.flush()
                result = {"mode": "apply" if args.apply else "dry-run", "created": dict(importer.counts),
                          "warnings": importer.warnings,
                          "legacy_references": "ShreckRPG notes and activities still refer to original game IDs"}
                if importer.warnings:
                    db.rollback()
                    result["status"] = "blocked_missing_world_mappings"
                elif args.apply:
                    db.commit()
                    result["status"] = "committed"
                else:
                    db.rollback()
                    result["status"] = "preview_only"
                print(json.dumps(result, indent=2))
                if importer.warnings:
                    raise SystemExit(2)
            except Exception:
                db.rollback()
                raise
    finally:
        rpg.close()
        shrecknet.close()


if __name__ == "__main__":
    main()
