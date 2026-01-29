from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.ontology import AuthorType, Cardinality, PropertyDataType


class AuthorMixin(BaseModel):
    author_type: AuthorType
    user_id: str | None = None
    agent_id: str | None = None

    @model_validator(mode="after")
    def check_author_fields(self) -> "AuthorMixin":
        if self.author_type == AuthorType.HUMAN:
            if not self.user_id:
                raise ValueError("user_id is required when author_type is human")
            self.agent_id = None
        elif self.author_type == AuthorType.AGENT:
            if not self.agent_id:
                raise ValueError("agent_id is required when author_type is agent")
            self.user_id = None

        if self.user_id and self.agent_id:
            raise ValueError("Only one of user_id or agent_id can be set")
        return self


class OntologyBase(BaseModel):
    name: str
    description: str | None = None
    image_url: str | None = Field(None, description="URL pointing to an ontology image")


class OntologyCreate(OntologyBase):
    pass


class OntologyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None


class OntologyRead(OntologyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class OntologyEntityBase(AuthorMixin):
    name: str
    description: str | None = None
    image_url: str | None = None
    keywords: list[str] = Field(default_factory=list)
    display_on_world: bool = True
    auto_generatable: bool = False


class OntologyEntityCreate(OntologyEntityBase):
    pass


class OntologyEntityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    keywords: list[str] | None = None
    display_on_world: bool | None = None
    auto_generatable: bool | None = None
    author_type: AuthorType | None = None
    user_id: str | None = None
    agent_id: str | None = None


class OntologyEntityRead(OntologyEntityBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ontology_id: int
    created_at: datetime
    updated_at: datetime


class OntologyPropertyBase(AuthorMixin):
    name: str
    description: str | None = None
    image_url: str | None = None
    cardinality: Cardinality
    data_type: PropertyDataType
    auto_generatable: bool = False


class OntologyPropertyCreate(OntologyPropertyBase):
    pass


class OntologyPropertyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    cardinality: Cardinality | None = None
    data_type: PropertyDataType | None = None
    auto_generatable: bool | None = None
    author_type: AuthorType | None = None
    user_id: str | None = None
    agent_id: str | None = None


class OntologyPropertyRead(OntologyPropertyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    created_at: datetime
    updated_at: datetime


class OntologyRelationshipBase(AuthorMixin):
    name: str
    description: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    bi_directional: bool = False
    destiny_entity_id: int | None = None
    auto_generatable: bool = False


class OntologyRelationshipCreate(OntologyRelationshipBase):
    pass


class OntologyRelationshipUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_urls: list[str] | None = None
    bi_directional: bool | None = None
    destiny_entity_id: int | None = None
    auto_generatable: bool | None = None
    author_type: AuthorType | None = None
    user_id: str | None = None
    agent_id: str | None = None


class OntologyRelationshipRead(OntologyRelationshipBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: int
    created_at: datetime
    updated_at: datetime


class OntologyCopyRequest(BaseModel):
    source_ontology_id: int


class OntologyCopyEntityResult(BaseModel):
    name: str
    properties: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    skipped_relationships: list[str] = Field(default_factory=list)


class OntologyCopyResponse(BaseModel):
    copied_entities: list[OntologyCopyEntityResult] = Field(default_factory=list)
    existing_entities: list[str] = Field(default_factory=list)
