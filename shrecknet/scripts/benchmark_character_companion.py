"""Measure deployed end-to-end cohorts, including true first token.

This command creates private benchmark chats/turns. Credentials come only from
SHRECKNET_URL and SHRECKNET_TOKEN; output never contains credentials or answer text.
Cases JSON: [{"cohort":"simple|normal|complex|image","text":"...","context":null}].
Images can be pre-uploaded explicitly and referenced as attachment_ids in cases.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4
import httpx


async def run(args):
    base=os.environ['SHRECKNET_URL']
    headers={'Authorization':'Bearer '+os.environ['SHRECKNET_TOKEN']}
    root='/users/me/character-companion'
    rows=[]
    async with httpx.AsyncClient(base_url=base,headers=headers,timeout=300) as client:
        response=await client.get(root+'/selection');response.raise_for_status();selection=response.json()
        if selection['availability']!='ready':raise ValueError('Select a prepared CharacterAgent before benchmarking')
        for case in json.loads(Path(args.cases).read_text()):
            response=await client.post(root+'/chats',json={'character_agent_id':selection['character_agent_id'],'selection_version':selection['version'],'title':'Companion benchmark'},headers={'Idempotency-Key':str(uuid4())});response.raise_for_status();chat=response.json()
            start=time.perf_counter()
            response=await client.post(root+f"/chats/{chat['id']}/turns",json={'text':case['text'],'context':case.get('context'),'selection_version':selection['version'],'attachment_ids':case.get('attachment_ids',[])},headers={'Idempotency-Key':str(uuid4())});response.raise_for_status();turn=response.json()
            row={'cohort':case['cohort'],'first_feedback_ms':None,'first_token_ms':None,'completion_ms':None}
            event=''
            async with client.stream('GET',turn['events_url'],headers={'Accept':'text/event-stream'}) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if line.startswith('event: '):event=line[7:]
                    if line.startswith('data: '):
                        elapsed=round((time.perf_counter()-start)*1000,2)
                        if row['first_feedback_ms'] is None:row['first_feedback_ms']=elapsed
                        if event=='response.delta' and row['first_token_ms'] is None:row['first_token_ms']=elapsed
                        if event in {'turn.completed','turn.failed','turn.cancelled'}:row['completion_ms']=elapsed;row['terminal_event']=event
            result=await client.get(turn['result_url']);result.raise_for_status()
            snapshot=result.json()
            row['metrics']=snapshot.get('metrics',{})
            row['usage']=(snapshot.get('result') or {}).get('usage')
            row['cost']=(snapshot.get('result') or {}).get('cost')
            rows.append(row)
    Path(args.output).write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',required=True);parser.add_argument('--output',required=True)
    asyncio.run(run(parser.parse_args()))
