from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import get_current_user, require_role
from app.models.model_user import User, UserRole
from app.config import settings
from pathlib import Path
from typing import List
import json
from pydantic import BaseModel

router = APIRouter(prefix="/jobs", tags=["Jobs"], dependencies=[Depends(get_current_user)])

JOB_DIRS = {
    "vectordb": settings.vectordb_job_dir,
    "writer": settings.writer_job_dir,
    "specialist": settings.specialist_job_dir,
    "novelist": settings.novelist_job_dir,
    "library": settings.library_job_dir,
}

class JobRef(BaseModel):
    kind: str
    job_id: str

class DeleteJobsRequest(BaseModel):
    jobs: List[JobRef]


def _load_jobs():
    jobs = []
    for kind, dir_path in JOB_DIRS.items():
        p = Path(dir_path)
        p.mkdir(parents=True, exist_ok=True)
        for f in p.glob("*.json"):
            with open(f) as fh:
                data = json.load(fh)
            data["job_id"] = f.stem
            data["kind"] = kind
            jobs.append(data)
    return jobs

@router.get("/")
async def list_jobs():
    jobs = _load_jobs()
    jobs.sort(key=lambda j: j.get("start_time", ""), reverse=True)
    return jobs

@router.delete("/")
async def delete_jobs(payload: DeleteJobsRequest, user: User = Depends(require_role(UserRole.system_admin))):
    deleted = []
    for ref in payload.jobs:
        dir_path = JOB_DIRS.get(ref.kind)
        if not dir_path:
            continue
        job_path = Path(dir_path) / f"{ref.job_id}.json"
        if not job_path.is_file():
            continue
        with open(job_path) as f:
            data = json.load(f)
        if data.get("status") in {"running", "processing", "queued"}:
            continue
        job_path.unlink()
        deleted.append({"kind": ref.kind, "job_id": ref.job_id})
    return {"deleted": deleted}
