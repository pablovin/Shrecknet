"""Export the implemented browser contracts without starting the full platform."""
import argparse
import json
from pathlib import Path

from fastapi import FastAPI
from app.api.routers.browser_auth import router

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
app = FastAPI(title="Shrecknet browser authentication", version="1")
app.include_router(router)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(app.openapi(), indent=2) + "\n")
