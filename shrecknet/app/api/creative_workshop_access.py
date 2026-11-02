"""Authorization shared by world-scoped workshop routes and run continuations."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, get_db_session, require_roles
from app.models.architect import ArchitectAnalysisRun
from app.models.novelist import NovelistRun
from app.models.user import User
from app.services.creative_workshop_service import require_workshop_world


def workshop_run_access(
    model: type[NovelistRun] | type[ArchitectAnalysisRun], role: str
):
    async def authorize(
        request: Request,
        actor: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> None:
        run_id = request.path_params.get("run_id")
        if not run_id:
            return
        run = await session.get(model, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        # Legacy callers retain their existing contract; agentless runs require the workshop role.
        if run.agent_id is None:
            require_roles(role)(actor)
            if run.ontology_id is None:
                raise HTTPException(status_code=404, detail="Run has no world")
            await require_workshop_world(session, run.ontology_id)

    return authorize
