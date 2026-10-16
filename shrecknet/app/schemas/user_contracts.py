from pydantic import BaseModel, ConfigDict


class PublicUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    avatar_url: str | None = None
