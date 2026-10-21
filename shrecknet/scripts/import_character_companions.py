"""Read-only dry run by default; run from shrecknet with PYTHONPATH=."""
import argparse
import asyncio
import json
from pathlib import Path
from app.jobs.companion.runner import database
from app.graph.neo4j import get_driver
from app.core.config_store import get_settings
from app.models.user import User
from app.services.character_agent_service import CharacterAgentService
from app.services.companion_import_service import import_companions


async def run(args):
    async with database() as session:
        async def lookup(user_id,character_id):
            user=await session.get(User,user_id)
            try:
                async with get_driver().session(database=get_settings().neo4j_database) as graph:
                    await CharacterAgentService(None,graph).get_agent(character_id,public_only=getattr(user.role,'value',user.role)!='admin')
                return True
            except Exception:
                return False
        result=await import_companions(session,args.source,args.chats,args.installation,json.loads(Path(args.user_map).read_text()),lookup,apply=args.apply,ownership_reviewed=args.ownership_reviewed)
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for option in ('source','chats','installation','user-map'):
        parser.add_argument('--'+option,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--ownership-reviewed',action='store_true')
    asyncio.run(run(parser.parse_args()))
