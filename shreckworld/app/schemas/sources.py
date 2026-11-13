"""Typed source-system contracts; trees are organization, not ontology entities."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal['narrative', 'explanatory']
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_:.\-]+$')]
Title = Annotated[str, Field(min_length=1, max_length=255)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class HierarchyInput(Contract):
    name: Title
    description: str = Field(default='', max_length=10000)
    section_types: list[Title] = Field(min_length=1, max_length=32)
    archived: bool = False
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def unique_types(self):
        if len(set(self.section_types)) != len(self.section_types):
            raise ValueError('Section type names must be unique')
        return self


class HierarchyRead(Contract):
    id: str
    version: int
    name: str
    description: str
    section_types: list[str]
    archived: bool
    has_image: bool


class SourceCreate(Contract):
    title: Title
    source_type: SourceType = 'narrative'
    hierarchy_template_id: str = 'rulebook'
    hierarchy_template_version: int = Field(default=1, ge=1)


class SourceSettings(SourceCreate):
    expected_revision: int = Field(ge=1)


class Paragraph(Contract):
    id: Identifier
    text: str = Field(max_length=200000)


class AuthoredContent(Contract):
    kind: Literal['authored'] = 'authored'
    paragraphs: list[Paragraph] = Field(default_factory=list, max_length=10000)


class ExtractedContent(Contract):
    kind: Literal['extracted'] = 'extracted'
    document_id: Identifier
    node_id: Identifier
    source_sha256: str = Field(pattern=r'^[a-fA-F0-9]{64}$')


ContentReference = Annotated[AuthoredContent | ExtractedContent, Field(discriminator='kind')]


class SectionMetadata(Contract):
    summary: str = Field(default='', max_length=10000)
    description: str = Field(default='', max_length=10000)
    tags: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=100)
    # Opaque, source-owned asset IDs. Never expose server filesystem paths.
    image_asset_id: Identifier | None = None
    markdown_asset_id: Identifier | None = None
    legacy_image_url: str | None = Field(default=None, max_length=2048)


class SourceSection(Contract):
    id: Identifier
    parent_id: Identifier | None
    section_type: Title
    title: Title
    order: int = Field(ge=0)
    content_reference: ContentReference = Field(default_factory=AuthoredContent)
    metadata: SectionMetadata = Field(default_factory=SectionMetadata)


class OrganizationInput(Contract):
    expected_revision: int = Field(ge=1)
    nodes: list[SourceSection] = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_tree(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError('Duplicate section ID')
        roots = [node for node in self.nodes if node.parent_id is None]
        if len(roots) != 1:
            raise ValueError('Exactly one source root is required')
        positions: set[tuple[str | None, int]] = set()
        paragraphs: set[str] = set()
        size = 0
        for node in self.nodes:
            if (node.parent_id, node.order) in positions:
                raise ValueError('Sibling order must be unique')
            positions.add((node.parent_id, node.order))
            visited = {node.id}
            current = node
            while current.parent_id is not None:
                if current.parent_id not in nodes or current.parent_id in visited:
                    raise ValueError('Invalid parent or cyclic tree')
                visited.add(current.parent_id)
                current = nodes[current.parent_id]
            if isinstance(node.content_reference, AuthoredContent):
                for paragraph in node.content_reference.paragraphs:
                    if paragraph.id in paragraphs:
                        raise ValueError('Duplicate paragraph ID')
                    paragraphs.add(paragraph.id)
                    size += len(paragraph.text.encode('utf-8'))
        if size > 2_000_000:
            raise ValueError('Authored content exceeds 2 MB organization limit')
        return self


class OrganizationRead(Contract):
    revision: int
    nodes: list[SourceSection]
    unresolved_section_ids: list[str] = Field(default_factory=list)
