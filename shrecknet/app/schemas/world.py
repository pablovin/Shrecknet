from pydantic import BaseModel, Field, model_validator


class WorldMetadata(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = Field(None, max_length=512)
    rpg_system: str | None = Field(None, max_length=255)
    rpg_edition: str | None = Field(None, max_length=100)
    rpg_subversion: str | None = Field(None, max_length=100)


class WorldUpdate(WorldMetadata):
    name: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_name_when_present(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("World name cannot be null")
        return self


class WorldRead(WorldMetadata):
    id: str
    ontology_ids: list[int]
    active_ontology_id: int | None
