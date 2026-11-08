"""Export frontend contracts without starting databases, workers or providers."""
import argparse
import json
from pathlib import Path
from fastapi import FastAPI
from app.api.routers.character_companion import router


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--sdk-schema-output')
    args=parser.parse_args()
    if args.sdk_schema_output:
        source=Path(__file__).resolve().parents[1] / 'app/schemas/companion_rich_response.py'
        header='"""Generated Companion rich-response boundary models.\n\nSource: shrecknet/app/schemas/companion_rich_response.py. Keep synchronized through\nshrecknet/scripts/export_character_companion_openapi.py --sdk-schema-output.\nIdentifiers never grant access.\n"""'
        Path(args.sdk_schema_output).write_text(source.read_text().replace(source.read_text().splitlines()[0],header,1))
    app=FastAPI(title='Shrecknet Character Companion',version='1')
    app.include_router(router)
    Path(args.output).write_text(json.dumps(app.openapi(),indent=2)+'\n')
