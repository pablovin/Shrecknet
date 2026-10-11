"""Read model roles, or update one role using administrator credentials.

SHRECKNET_TOKEN=... python 02_model_configuration.py
SHRECKNET_TOKEN=... python 02_model_configuration.py --role psychological_interpretation \
    --provider openrouter --model your-configured-model --reasoning --reasoning-effort low
"""
import argparse
import asyncio
import os

from shrecknet_client import Shrecknet
from shrecknet_client.models import CharacterAgentModelConfigUpdate, LLMModelTarget


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['character_incorporation', 'scene_interpretation',
                                         'psychological_interpretation', 'decision_making'])
    parser.add_argument('--provider')
    parser.add_argument('--model')
    parser.add_argument('--reasoning', action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--reasoning-effort", choices=["minimal", "low", "medium", "high", "xhigh", "max"])
    args = parser.parse_args()
    if args.role and (not args.provider or not args.model):
        parser.error('--role requires --provider and --model')
    if not args.role and (args.provider or args.model or args.reasoning_effort):
        parser.error('--provider, --model, and --reasoning-effort require --role')
    token = os.environ.get('SHRECKNET_TOKEN')
    if not token:
        parser.error('Set SHRECKNET_TOKEN to an administrator bearer token')
    async with Shrecknet(base_url=os.environ.get('SHRECKNET_BASE_URL', 'http://localhost:8100'), token=token) as sdk:
        if args.role:
            payload = CharacterAgentModelConfigUpdate(**{
                f'model_character_agent_{args.role}': LLMModelTarget(
                    provider=args.provider, name=args.model, reasoning=args.reasoning,
                    reasoning_effort=args.reasoning_effort),
            })
            result = await sdk.character_agents.update_model_config(payload)
        else:
            result = await sdk.character_agents.get_model_config()
        print(result.model_dump_json(indent=2))


if __name__ == '__main__':
    asyncio.run(main())
