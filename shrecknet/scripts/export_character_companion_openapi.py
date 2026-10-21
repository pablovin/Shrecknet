"""Export frontend contracts without starting databases, workers or providers."""
import argparse
import json
from pathlib import Path
from fastapi import FastAPI
from app.api.routers.character_companion import router


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    app=FastAPI(title='Shrecknet Character Companion',version='1')
    app.include_router(router)
    Path(args.output).write_text(json.dumps(app.openapi(),indent=2)+'\n')
