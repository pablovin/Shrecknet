"""Generate/recreate an identity, inspect evidence, edit a point, and query it.

Requires the matching backend/worker release and administrator token. Starting a
draft replaces any existing CharacterAgent for the supplied canonical entity.
Run with --ontology-id, --entity-id, and SHRECKNET_TOKEN in the environment.
"""
import argparse
import asyncio
import os
import time

from shrecknet_client import Shrecknet
from shrecknet_client.character_traits import TraitEdit
from shrecknet_client.models import (
    CharacterAgentCreateRequest, CharacterAgentEmbeddedAspect, CharacterAgentEmbeddedGoal,
    CharacterAgentQueryRequest, CharacterAgentUpdate, EmbodimentDraftCreate,
)


async def main(args):
    async with Shrecknet(base_url=args.base_url, token=os.environ['SHRECKNET_TOKEN']) as sdk:
        metadata = await sdk.character_agents.trait_definitions()
        print('Trait specification:', metadata['version'])
        started = await sdk.character_agents.start_embodiment(
            EmbodimentDraftCreate(ontology_id=args.ontology_id, entity_instance_id=args.entity_id))
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            draft = await sdk.character_agents.get_embodiment(started.draft_id)
            if draft.status == 'failed':
                raise RuntimeError(draft.error_message)
            if draft.status == 'ready':
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError('Draft is still running; retain its ID and poll again.')
        proposal = draft.proposal
        agent = await sdk.character_agents.create(CharacterAgentCreateRequest(
            ontology_id=args.ontology_id, entity_instance_id=args.entity_id, embodiment_draft_id=draft.id,
            name=proposal['name'], background_story=proposal['background_story'], subtitle=proposal.get('subtitle'),
            aspects=[CharacterAgentEmbeddedAspect.model_validate(item) for item in proposal['aspects']],
            goals=[CharacterAgentEmbeddedGoal.model_validate(item) for item in proposal['goals']]))
        print('Inferred integrity:', agent.trait_profile.dispositional_traits['integrity'].model_dump())
        print('Evidence:', [item.model_dump() for item in await sdk.character_agents.list_trait_evidence(agent.id)])
        await sdk.character_agents.update(agent.id, CharacterAgentUpdate(trait_edits={
            'integrity': TraitEdit(point=8, reason='Example authored sheet override.')}))
        print('Changes:', [item.model_dump() for item in await sdk.character_agents.list_identity_changes(agent.id, change_type='trait')])
        queued = await sdk.character_agents.query(agent.id, CharacterAgentQueryRequest(
            query='You can keep an untraceable overpayment or return it. What do you choose?',
            context={'knows_overpayment':True,'can_return':True,'free_choice':True}))
        result = await sdk.character_agents.wait_for_query(agent.id, queued.job_id)
        if result.status == 'failed':
            raise RuntimeError(result.error.message)
        print(result.result.content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://localhost:8100')
    parser.add_argument('--ontology-id',type=int,required=True)
    parser.add_argument('--entity-id',required=True)
    parser.add_argument('--timeout',type=float,default=900)
    asyncio.run(main(parser.parse_args()))
