"""Caller-selected world scope; all is resolved when a conversation starts."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ElderWorldScope(BaseModel):
    mode: Literal["selected", "all"] = "selected"
    ontology_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self):
        if any(value < 1 for value in self.ontology_ids):
            raise ValueError("World IDs must be positive")
        self.ontology_ids = sorted(set(self.ontology_ids))
        if self.mode == "selected" and not self.ontology_ids:
            raise ValueError("Select at least one world")
        if self.mode == "all" and self.ontology_ids:
            raise ValueError("All-world scope must not contain a selected list")
        return self
