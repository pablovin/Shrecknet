from fastapi import FastAPI
from sqlmodel import SQLModel
from .database import init_db
from .api import (
    api_characteristic,
    api_concept,
    api_page,
    api_user,
    api_gameworld,
    api_import_export,
    api_vectordb,
    api_agent,
    api_specialist,
    api_backup,
    api_library,
)
from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware

import logging
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
logging.getLogger("fastapi").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context: setup and teardown logic.
    This replaces the deprecated on_event('startup') decorator.
    """
    await init_db()
    yield
    # Optional: add teardown logic here

app = FastAPI(
    title="Shrecknet RPG World Manager",
    description="Backend API for RPG campaign/world management.",
    version="0.1.0",
    lifespan=lifespan,root_path="/backend_api"
)

origins = settings.allowed_origins.split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Or ["*"] for all, but less secure!
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# app.include_router(
#     fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
# )

# Auth endpoints (JWT)
# app.include_router(
#     fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
# )
# # Registration
# app.include_router(
#     fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"]
# )
# # User management (current user info, etc)
# app.include_router(
#     fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
# )

app.include_router(api_user.router)
app.include_router(api_gameworld.router)
app.include_router(api_concept.router)
app.include_router(api_characteristic.router)
app.include_router(api_page.router)
app.include_router(api_import_export.router)
app.include_router(api_vectordb.router)
app.include_router(api_agent.router)
app.include_router(api_specialist.router)
app.include_router(api_backup.router)
app.include_router(api_library.router)



# from app.api.test_auth import router as test_auth_router
# app.include_router(test_auth_router)