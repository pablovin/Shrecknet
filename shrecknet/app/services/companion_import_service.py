"""Offline, resumable read-only import of standalone CharacterAgent companions.

Never import or start the legacy CompanionStore: its initialization can delete
historical chats. Explicit reviewed user mappings are required for writes.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from app.models.user import User
from app.models.character_companion import CompanionImport, CompanionSelection, CompanionRapport, CompanionRapportAudit, CompanionChat, CompanionTurn, CompanionMessage
from app.schemas.character_companion import DIMENSIONS


def timestamp(value):
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def legacy_rows(connection, table):
    allowed={'user_character_agent_selection','user_character_rapport','user_character_rapport_history','sessions'}
    if table not in allowed:
        raise ValueError('Unsupported import table')
    exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    return [dict(row) for row in connection.execute(f'SELECT * FROM {table}')] if exists else []


async def import_companions(session, source, chats_dir, installation, user_mapping, character_lookup, *, apply=False, ownership_reviewed=False):
    if apply and not ownership_reviewed:
        raise ValueError('Apply requires reviewed legacy user ownership mappings')
    source, chats_dir=Path(source).resolve(), Path(chats_dir).resolve()
    report={'imported':0,'already_imported':0,'conflicts':0,'quarantined':0,'missing_files':0,'dry_run':not apply}
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        names={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not names.intersection({'user_character_agent_selection','user_character_rapport','user_character_rapport_history','sessions'}):
            raise ValueError('Unsupported legacy schema: no recognized Companion tables')
        rows={table:legacy_rows(connection,table) for table in ('user_character_agent_selection','user_character_rapport','user_character_rapport_history','sessions')}
    async def destination(row):
        user_id=user_mapping.get(str(row['user_id']))
        if user_id is None or not await session.get(User,int(user_id)):
            report['quarantined']+=1
            return None
        character=row.get('character_agent_id') or row.get('companion_id')
        if not character or not await character_lookup(int(user_id),character):
            report['quarantined']+=1
            return None
        return int(user_id), character
    async def admit(kind, identifier, data):
        key=f'{installation}:{kind}:{identifier}'
        checksum=hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()
        old=await session.get(CompanionImport,key)
        if old:
            report['already_imported' if old.checksum==checksum else 'conflicts']+=1
            return None
        return key,checksum
    def mapped(kind,key):
        return str(uuid5(NAMESPACE_URL,f'{installation}:{kind}:{key}'))
    def remember(info,destination_id):
        report['imported']+=1
        if apply:
            session.add(CompanionImport(source_key=info[0],checksum=info[1],destination_id=str(destination_id)))
    for table in ('user_character_rapport','user_character_agent_selection','user_character_rapport_history','sessions'):
        for row in rows[table]:
            owner=await destination(row)
            if not owner:
                continue
            user_id,character=owner
            source_id=row.get('session_id') or f"{row['user_id']}:{character}:{row.get('snapshot_date','')}"
            data=dict(row)
            chat=None
            if table=='sessions':
                file=(chats_dir/str(row['user_id'])/character/f"{row['session_id']}.json").resolve()
                if not file.is_relative_to(chats_dir) or not file.is_file():
                    report['missing_files']+=1
                    continue
                try:
                    chat=json.loads(file.read_text())
                    if not isinstance(chat,dict):
                        raise ValueError('Invalid chat document')
                except (OSError, ValueError):
                    report['quarantined']+=1
                    continue
                if int(chat.get('user_id',row['user_id']))!=row['user_id'] or str(chat.get('companion_id',character))!=character:
                    report['quarantined']+=1
                    continue
                data['chat']=chat
            info=await admit(table,source_id,data)
            if info is None:
                continue
            if table=='user_character_rapport':
                if await session.get(CompanionRapport,(user_id,character)):
                    report['conflicts']+=1
                    continue
                scores={key:row.get(key,default) for key,default in DIMENSIONS.items()}
                if any(type(value) is not int or not 0<=value<=100 for value in scores.values()):
                    report['quarantined']+=1
                    continue
                if apply:
                    session.add(CompanionRapport(user_id=user_id,character_agent_id=character,**scores,interaction_count=row.get('interaction_count',0),updated_at=timestamp(row.get('updated_at'))))
                    session.add(CompanionRapportAudit(user_id=user_id,character_agent_id=character,disposition='import_baseline',data={'source':info[0],'legacy':row}))
            elif table=='user_character_agent_selection':
                if await session.get(CompanionSelection,user_id):
                    report['conflicts']+=1
                    continue
                if apply:
                    session.add(CompanionSelection(user_id=user_id,character_agent_id=character,version=1,selected_at=timestamp(row.get('selected_at')),updated_at=timestamp(row.get('updated_at'))))
            elif table=='user_character_rapport_history':
                if apply:
                    session.add(CompanionRapportAudit(user_id=user_id,character_agent_id=character,disposition='import_snapshot',data={'source':info[0],'legacy':row},created_at=timestamp(row.get('snapshot_date'))))
            else:
                chat_id=mapped('chat',row['session_id'])
                if await session.get(CompanionChat,chat_id):
                    report['conflicts']+=1
                    continue
                if apply:
                    session.add(CompanionChat(id=chat_id,user_id=user_id,character_agent_id=character,title=row.get('title') or 'Imported conversation',default_scope={'surface':'atlas','ontology_id':row['ontology_id']},created_at=timestamp(row.get('created_at')),updated_at=timestamp(row.get('updated_at'))))
                    await session.flush()
                    messages=chat.get('messages',[])
                    index=0
                    while index<len(messages):
                        first=messages[index]
                        if not isinstance(first,dict) or not isinstance(first.get('content'),str):
                            report['quarantined']+=1
                            index+=1
                            continue
                        entries=[(index,first)]
                        complete=False
                        if first.get('role')=='user' and index+1<len(messages):
                            second=messages[index+1]
                            if isinstance(second,dict) and second.get('role')=='assistant' and isinstance(second.get('content'),str) and (second.get('meta') or second.get('metadata') or {}).get('message_kind')!='interim':
                                entries.append((index+1,second))
                                complete=True
                        turn_id=mapped('turn',f"{row['session_id']}:{index}")
                        delivered=entries[-1][1]['content'] if complete else None
                        created=timestamp(first.get('created_at') or first.get('ts') or first.get('timestamp') or row.get('created_at'))
                        session.add(CompanionTurn(id=turn_id,chat_id=chat_id,user_id=user_id,character_agent_id=character,status='completed' if complete else 'interrupted',principal={'expires_at':0,'sid':None},request={'text':first['content'] if first.get('role')=='user' else '', 'attachment_ids':[]},result={'text':delivered,'citations':[], 'legacy_messages':[entry for _,entry in entries], 'warnings':['Imported legacy references require resolution']},created_at=created,rapport_status='not_applicable'))
                        await session.flush()
                        for pos,entry in entries:
                            role=entry.get('role','legacy')
                            interim=(entry.get('meta') or entry.get('metadata') or {}).get('message_kind')=='interim'
                            session.add(CompanionMessage(id=mapped('message',f"{row['session_id']}:{pos}"),turn_id=turn_id,chat_id=chat_id,role='interim' if interim else role,content=entry['content'],manifest=[],committed=complete,created_at=timestamp(entry.get('created_at') or entry.get('ts') or entry.get('timestamp') or row.get('created_at'))))
                        index+=len(entries)
                source_id=chat_id
            remember(info,source_id)
            if apply:
                await session.flush()
    if apply:
        await session.commit()
    return report
