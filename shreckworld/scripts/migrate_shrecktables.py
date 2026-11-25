"""Preview and optionally import ShreckRPG games and sessions into ShreckTables.

Dry-run is the default. Interactive mode prints a field/count report, then asks
for a backup acknowledgement and a typed confirmation before committing.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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


def source_timestamp(value):
    return date(value) or datetime.now(timezone.utc)


def print_report(result):
    print("\nShreckRPG → ShreckTables migration preview")
    print("=" * 38)
    print(f"Tables: {len(result['tables'])}; planned records: {dict(result['created'])}")
    columns = [
        ("table", 24, "table_name"), ("world mapping", 30, "mapping"),
        ("users", 8, "members"), ("sessions", 9, "sessions"),
        ("unlinked users", 14, "participation_users_without_membership"),
        ("attendance", 11, "attendance"), ("polls", 7, "polls"),
        ("votes", 7, "votes"), ("result", 28, "plan"),
    ]
    print(" | ".join(title.ljust(width) for title, width, _ in columns))
    print("-+-".join("-" * width for _, width, _ in columns))
    for item in result["tables"]:
        mapping = f"{item.get('ontology_name') or '?'} → {item.get('world_name') or item['world_status']}"
        values = {**item, "mapping": mapping}
        print(" | ".join(str(values.get(key, ""))[:width].ljust(width)
                         for _, width, key in columns))
    if not result["tables"]:
        print("No ShreckRPG tables found.")
    print("\nSource records: " + ", ".join(
        f"{name} {count}" for name, count in result["source_totals"].items()))
    for issue in result["world_mapping_issues"]:
        print(f"BLOCKED ontology {issue['ontology_id']} ({issue.get('ontology_name') or 'unknown'}): {issue['status']}")
    for issue in result["user_mapping_issues"]:
        details = []
        if issue["missing_user_ids"]:
            details.append("not found in Shrecknet: " + ", ".join(issue["missing_user_ids"]))
        if issue["nonmember_user_ids"]:
            details.append("attendance/vote users absent from table membership: " + ", ".join(issue["nonmember_user_ids"]))
        print(f"BLOCKED table {issue['table_id']}: {'; '.join(details)}")
    print("\nMigrated fields")
    for field in result["fields"]["migrated"]:
        print(f"  ✓ {field}")
    print("Defaulted or derived fields")
    for field in result["fields"]["defaulted"]:
        print(f"  • {field}")
    print("Not migrated by this operation")
    for field in result["fields"]["not_migrated"]:
        print(f"  ! {field}")


def normalized_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def resolve_world_names(shrecknet):
    """Resolve ontology IDs only when the ontology and canonical world names match uniquely."""
    worlds = rows(shrecknet, "worlds")
    ontologies = rows(shrecknet, "ontologies")
    world_names = {}
    for world in worlds:
        world_names.setdefault(normalized_name(world.get("name")), []).append(world)
    result = {}
    for ontology in ontologies:
        name = ontology.get("name")
        matches = world_names.get(normalized_name(name), [])
        if len(matches) == 1:
            matched = matches[0]
            linked_world_id = ontology.get("world_id")
            if linked_world_id and str(linked_world_id) != str(matched["id"]):
                result[str(ontology["id"])] = {
                    "ontology_name": name, "world_id": None,
                    "world_name": matched.get("name"), "status": "name_link_conflict",
                }
            else:
                result[str(ontology["id"])] = {
                    "ontology_name": name, "world_id": str(matched["id"]),
                    "world_name": matched.get("name"), "status": "matched",
                }
        else:
            result[str(ontology["id"])] = {
                "ontology_name": name, "world_id": None,
                "world_name": None,
                "status": "unmatched" if not matches else "ambiguous",
            }
    return result


class Importer:
    def __init__(self, db, world_lookup, apply, known_user_ids=None):
        self.db = db
        self.world_lookup = {
            str(key): (value if isinstance(value, dict) else {
                "ontology_name": None, "world_id": str(value),
                "world_name": "explicit mapping", "status": "override",
            })
            for key, value in world_lookup.items()
        }
        self.apply = apply
        self.counts = Counter()
        self.warnings = []
        self.known_user_ids = {str(value) for value in known_user_ids} if known_user_ids is not None else None
        self.user_warnings = []

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
        if not value or not value.get("world_id"):
            status = value.get("status", "ontology_not_found") if value else "ontology_not_found"
            self.warnings.append({"ontology_id": ontology_id,
                                  "ontology_name": value.get("ontology_name") if value else None,
                                  "status": status})
            return None
        return value["world_id"]

    def import_rpg(self, rpg):
        games = rows(rpg, "games")
        memberships = rows(rpg, "game_memberships")
        sessions = rows(rpg, "game_sessions")
        attendance = rows(rpg, "game_session_attendance")
        polls = rows(rpg, "game_session_polls")
        options = rows(rpg, "game_session_poll_options")
        votes = rows(rpg, "game_session_poll_votes")
        game_map = {}
        membership_by_game = {}
        for membership in memberships:
            membership_by_game.setdefault(membership["game_id"], []).append(membership)
        sessions_by_game = {}
        for session in sessions:
            sessions_by_game.setdefault(session["game_id"], []).append(session)
        self.table_report = []
        for game in games:
            legacy = game["id"]
            world_id = self.world(game["ontology_id"])
            ontology = self.world_lookup.get(str(game["ontology_id"]), {})
            old_sessions = sessions_by_game.get(legacy, [])
            related_session_ids = {item["id"] for item in old_sessions}
            game_poll_ids = {p["id"] for p in polls if p.get("session_id") in related_session_ids}
            game_option_ids = {o["id"] for o in options if o.get("poll_id") in game_poll_ids}
            participant_ids = {int(game["owner_user_id"]),
                               *(int(m["user_id"]) for m in membership_by_game.get(legacy, []))}
            attendance_user_ids = {int(item["user_id"]) for item in attendance
                                   if item.get("session_id") in related_session_ids}
            vote_user_ids = {int(item["user_id"]) for item in votes
                             if item.get("option_id") in game_option_ids}
            participation_user_ids = attendance_user_ids | vote_user_ids
            report = {
                "legacy_table_id": legacy, "table_name": game.get("name"),
                "ontology_id": game.get("ontology_id"), "ontology_name": ontology.get("ontology_name"),
                "world_name": ontology.get("world_name"), "world_status": ontology.get("status", "ontology_not_found"),
                "members": len(participant_ids),
                "participation_users_without_membership": len(participation_user_ids - participant_ids),
                "sessions": len(old_sessions),
                "attendance": sum(item.get("session_id") in related_session_ids for item in attendance),
                "polls": len(game_poll_ids), "votes": sum(item.get("option_id") in game_option_ids for item in votes),
                "plan": "blocked" if not world_id else "migrate",
            }
            self.table_report.append(report)
            if not world_id:
                self.counts["skipped_games"] += 1
                continue
            table = self.lookup("rpg_game", legacy, Table)
            existed = table is not None
            if not table:
                table = self.mark("rpg_game", legacy, Table(
                    ontology_id=game["ontology_id"], world_id=world_id,
                    name=game["name"], created_by=str(game["owner_user_id"]),
                    calendar_id=game.get("google_calendar_id"), vtt_url=game.get("vtt"),
                    created_at=source_timestamp(game.get("created_at")),
                    updated_at=source_timestamp(game.get("updated_at") or game.get("created_at"))))
            game_map[legacy] = table.id
            participants = {int(game["owner_user_id"]): "gm"}
            for member in membership_by_game.get(legacy, []):
                if int(member["user_id"]) not in participants:
                    participants[int(member["user_id"])] = "gm" if member.get("role") in {"gm", "owner"} else "player"
            referenced_user_ids = set(participants) | participation_user_ids
            missing_users = sorted(str(user_id) for user_id in referenced_user_ids
                                   if self.known_user_ids is not None and str(user_id) not in self.known_user_ids)
            nonmembers = sorted(str(user_id) for user_id in participation_user_ids - set(participants))
            if missing_users or nonmembers:
                self.user_warnings.append({"table_id": legacy, "missing_user_ids": missing_users,
                                           "nonmember_user_ids": nonmembers})
            for user_id, role in participants.items():
                if not self.db.scalar(select(TableMember.id).where(
                    TableMember.table_id == table.id, TableMember.user_id == str(user_id))):
                    legacy_member = next((m for m in membership_by_game.get(legacy, [])
                                          if int(m["user_id"]) == user_id), {})
                    member_values = {"table_id": table.id, "user_id": str(user_id), "role": role}
                    if legacy_member.get("joined_at"):
                        member_values["joined_at"] = date(legacy_member["joined_at"])
                    self.db.add(TableMember(**member_values))
                    self.counts["members"] += 1
            report["plan"] = "mapped; reconcile children" if existed else "migrate"
            if missing_users or nonmembers:
                report["plan"] = "blocked_user_associations"
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
                    created_by=str(next(g["owner_user_id"] for g in games if g["id"] == source["game_id"])),
                    created_at=source_timestamp(source.get("created_at")),
                    updated_at=source_timestamp(source.get("updated_at") or source.get("created_at"))))
            session_map[source["id"]] = row.id
        self.db.flush()
        for item in attendance:
            sid = session_map.get(item["session_id"])
            if sid is None:
                continue
            if not self.db.scalar(select(TableAttendance.id).where(
                TableAttendance.session_id == sid, TableAttendance.user_id == str(item["user_id"]))):
                self.db.add(TableAttendance(session_id=sid, user_id=str(item["user_id"]),
                    attending=bool(item["attending"]), responded_at=source_timestamp(item.get("responded_at"))))
                self.counts["attendance"] += 1
        poll_map = {}
        for item in polls:
            sid = session_map.get(item["session_id"])
            if sid is None:
                continue
            poll = self.lookup("rpg_poll", item["id"], TablePoll)
            if not poll:
                poll = self.mark("rpg_poll", item["id"], TablePoll(
                    session_id=sid, is_finalized=bool(item.get("is_finalized")),
                    created_at=source_timestamp(item.get("created_at"))))
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
                    option_id=oid, user_id=str(item["user_id"]),
                    created_at=source_timestamp(item.get("created_at"))))
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
    parser.add_argument("--world-map", type=Path, help="Optional JSON override mapping ontology IDs to canonical world IDs")
    mode = parser.add_mutually_exclusive_group()
    parser.add_argument("--apply", action="store_true", help="Apply after supplying both explicit acknowledgements")
    parser.add_argument("--backup-confirmed", action="store_true", help="Acknowledge restorable backups for non-interactive apply")
    parser.add_argument("--confirmation", help="Must equal MIGRATE SHRECKTABLES for non-interactive apply")
    mode.add_argument("--interactive", action="store_true", help="Show the report and prompt before applying")
    args = parser.parse_args()
    if args.apply and args.interactive:
        parser.error("Choose either --interactive or guarded non-interactive --apply")
    if args.apply and (not args.backup_confirmed or args.confirmation != "MIGRATE SHRECKTABLES"):
        parser.error("--apply requires --backup-confirmed and --confirmation 'MIGRATE SHRECKTABLES'")

    rpg = connect_readonly(args.rpg_db)
    shrecknet = connect_readonly(args.shrecknet_db)
    try:
        world_lookup = resolve_world_names(shrecknet)
        if not shrecknet.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone():
            raise RuntimeError("Shrecknet database is missing the users table")
        known_user_ids = {str(row["id"]) for row in rows(shrecknet, "users")}
        if args.world_map:
            overrides = json.loads(args.world_map.read_text())
            for ontology_id, world_id in overrides.items():
                world_lookup[str(ontology_id)] = {"ontology_name": world_lookup.get(str(ontology_id), {}).get("ontology_name"),
                    "world_id": str(world_id), "world_name": "explicit override", "status": "override"}
        if not args.target_db.exists():
            raise FileNotFoundError("Target database not found. Initialize ShreckWorld first.")
        engine = create_engine("sqlite:///" + str(args.target_db.resolve()),
                               connect_args={"check_same_thread": False})
        with Session(engine, autoflush=False) as db:
            try:
                importer = Importer(db, world_lookup, args.apply or args.interactive, known_user_ids)
                importer.import_rpg(rpg)
                importer.import_engine()
                db.flush()
                result = {"mode": "apply" if args.apply else "preview", "created": dict(importer.counts),
                          "tables": importer.table_report, "world_mapping_issues": importer.warnings,
                          "user_mapping_issues": importer.user_warnings,
                          "source_totals": {name: len(rows(rpg, name)) for name in (
                              "games", "game_memberships", "game_sessions", "game_session_attendance",
                              "game_session_polls", "game_session_poll_options", "game_session_poll_votes")},
                          "fields": {
                              "migrated": ["Table: legacy ID mapping, ontology ID, matched world ID, name, owner, calendar ID, VTT URL, created/updated timestamps", "Members: user ID, owner/GM/player role, joined timestamp when present", "Session: legacy ID mapping, title, summary, location, scheduled date, timezone, Google event ID, Meet URL, creator, created/updated timestamps", "Attendance: user ID, response, response timestamp", "Poll: legacy ID mapping, finalized state, created timestamp, finalized option", "Poll option: legacy ID mapping and proposed date", "Poll vote: legacy ID mapping, user ID and created timestamp"],
                              "defaulted": ["Table description empty, no image, timezone UTC, standard ShreckTables defaults", "Session duration 180 minutes; scheduled status when a date exists, otherwise draft", "Calendar sync status marked legacy_external when an event ID exists; no calendar event is recreated"],
                              "not_migrated": ["ShreckRPG notes/activities and their legacy game references", "Google account ownership or calendar event mutations", "source file attachments not represented in the legacy session schema", "Poll-option created timestamps because ShreckTables has no destination field"],
                          },
                          "legacy_references": "ShreckRPG notes and activities still refer to original game IDs"}
                print_report(result)
                if importer.warnings or importer.user_warnings:
                    db.rollback()
                    result["status"] = "blocked_association_issues"
                    raise SystemExit(2)
                elif args.interactive:
                    if not sys.stdin.isatty():
                        db.rollback()
                        print("Interactive confirmation requires a terminal; no changes were committed.")
                        raise SystemExit(2)
                    answer = input("\nHave you taken and verified restorable backups of ShreckRPG, Shrecknet and ShreckWorld? Type YES: ").strip()
                    if answer != "YES":
                        db.rollback()
                        print("Backup acknowledgement not received; no changes were committed.")
                        return
                    answer = input("Type MIGRATE SHRECKTABLES to commit this migration: ").strip()
                    if answer != "MIGRATE SHRECKTABLES":
                        db.rollback()
                        print("Confirmation did not match; no changes were committed.")
                        return
                    db.commit()
                    print("Migration committed.")
                elif args.apply:
                    db.commit()
                    print("Migration committed.")
                else:
                    db.rollback()
                    print("Preview only. No changes were committed.")
            except Exception:
                db.rollback()
                raise
    finally:
        rpg.close()
        shrecknet.close()


if __name__ == "__main__":
    main()
