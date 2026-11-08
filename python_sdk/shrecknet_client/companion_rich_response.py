"""Generated Companion rich-response boundary models.

Source: shrecknet/app/schemas/companion_rich_response.py. Keep synchronized through
shrecknet/scripts/export_character_companion_openapi.py --sdk-schema-output.
Identifiers never grant access.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
Text = Annotated[str, Field(max_length=4000)]
EvidenceIDs = Annotated[list[Identifier], Field(max_length=20)]
Scalar = str | int | float | bool | None
BLOCK_TYPES = ['narrative', 'image', 'gallery', 'entity_card', 'table', 'book_excerpt']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AtlasReference(Contract):
    ontology_id: int = Field(gt=0)
    node_id: Identifier
    node_label: Literal['EntityInstance', 'Scene', 'Milestone']


class BookReference(Contract):
    ontology_id: int | None = Field(None, gt=0)
    shreckworld_id: Identifier | None = None
    library_item_id: Identifier
    document_id: Identifier
    source_sha256: Identifier
    page_number: int = Field(ge=1)
    page_label: str | None = Field(None, max_length=128)

    @model_validator(mode='after')
    def scope(self):
        if bool(self.ontology_id) == bool(self.shreckworld_id):
            raise ValueError('one_book_scope_required')
        return self


class ChronicleReference(Contract):
    chronicle_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    section_id: Identifier | None = None


class FoundryReference(Contract):
    foundry_world_id: Identifier
    uuid: str = Field(min_length=3, max_length=512, pattern=r'^(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]+$')
    source_revision: str | None = Field(None, max_length=128)


class WebReference(Contract):
    url: str = Field(max_length=2048)

    @field_validator('url')
    @classmethod
    def public_scheme(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('invalid_web_target')
        return value


class TargetBase(Contract):
    id: Identifier


class AtlasTarget(TargetBase):
    kind: Literal['atlas']
    reference: AtlasReference


class BookTarget(TargetBase):
    kind: Literal['shreckworld']
    reference: BookReference


class ChronicleTarget(TargetBase):
    kind: Literal['chronicle']
    reference: ChronicleReference


class FoundryTarget(TargetBase):
    kind: Literal['foundry']
    reference: FoundryReference


class WebTarget(TargetBase):
    kind: Literal['web']
    reference: WebReference


Target = Annotated[AtlasTarget | BookTarget | ChronicleTarget | FoundryTarget | WebTarget, Field(discriminator='kind')]


class ServiceDelivery(Contract):
    kind: Literal['service']
    service: Literal['shrecknet', 'shreckworld']
    path: str = Field(max_length=2048, pattern=r'^/[A-Za-z0-9/_?=.&%-]+$')

    @field_validator('path')
    @classmethod
    def relative(cls, value):
        if value.startswith('//') or '..' in value:
            raise ValueError('invalid_service_path')
        return value


class LocalDelivery(Contract):
    kind: Literal['foundry_local']
    reference: FoundryReference


class ExternalDelivery(Contract):
    kind: Literal['external']
    url: str = Field(max_length=2048)

    @field_validator('url')
    @classmethod
    def scheme(cls, value):
        return WebReference.public_scheme(value)


class Asset(Contract):
    id: Identifier
    source_type: Literal['atlas', 'shreckworld', 'foundry', 'web']
    evidence_ids: EvidenceIDs
    source_revision: str | None = Field(None, max_length=512)
    provenance_kind: Literal['entity_image', 'scene_image', 'page_thumbnail', 'book_illustration', 'document_image', 'provider_image']
    mime_type: Literal['image/png', 'image/jpeg', 'image/webp'] | None = None
    width: int | None = Field(None, gt=0, le=20000)
    height: int | None = Field(None, gt=0, le=20000)
    attribution: str = Field(max_length=1000)
    target_id: Identifier
    delivery_ref: Annotated[ServiceDelivery | LocalDelivery | ExternalDelivery, Field(discriminator='kind')]


class Block(Contract):
    id: Identifier
    evidence_ids: EvidenceIDs
    target_id: Identifier | None = None


class Narrative(Block):
    type: Literal['narrative']
    segment_ids: list[Identifier] = Field(min_length=1, max_length=20)


class Visual(Block):
    asset_id: Identifier
    alt: str = Field(max_length=255)
    caption: str | None = Field(None, max_length=1000)
    fallback_text: Text


class Image(Visual):
    type: Literal['image']


class GalleryItem(Contract):
    asset_id: Identifier
    alt: str = Field(max_length=255)
    caption: str | None = Field(None, max_length=1000)
    evidence_ids: EvidenceIDs
    target_id: Identifier | None = None


class Gallery(Block):
    type: Literal['gallery']
    items: list[GalleryItem] = Field(min_length=1, max_length=4)
    fallback_text: Text


class EntityCard(Block):
    type: Literal['entity_card']
    entity_ref: AtlasReference
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    asset_id: Identifier | None = None
    fallback_text: Text


class Column(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=255)


class Cell(Contract):
    value: Annotated[str, Field(max_length=500)] | int | float | bool | None
    evidence_ids: EvidenceIDs


class Row(Contract):
    id: Identifier
    cells: list[Cell] = Field(min_length=1, max_length=8)
    evidence_ids: EvidenceIDs
    target_id: Identifier | None = None


class Table(Block):
    type: Literal['table']
    title: str = Field(min_length=1, max_length=255)
    columns: list[Column] = Field(min_length=1, max_length=8)
    rows: list[Row] = Field(min_length=1, max_length=20)
    fallback_text: Text

    @model_validator(mode='after')
    def rectangular(self):
        if len({c.id for c in self.columns}) != len(self.columns) or len({r.id for r in self.rows}) != len(self.rows):
            raise ValueError('duplicate_table_ids')
        if any(len(row.cells) != len(self.columns) for row in self.rows):
            raise ValueError('non_rectangular_table')
        return self


class Excerpt(Block):
    type: Literal['book_excerpt']
    quote: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=255)
    document_ref: BookReference
    asset_id: Identifier | None = None
    fallback_text: Text


RichBlock = Annotated[Narrative | Image | Gallery | EntityCard | Table | Excerpt, Field(discriminator='type')]


class RichResponse(Contract):
    version: Literal[1] = 1

    @field_validator('version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('integer_version_required')
        return value
    blocks: list[RichBlock] = Field(min_length=1, max_length=8)
    assets: list[Asset] = Field(max_length=4)
    targets: list[Target] = Field(max_length=20)

    @model_validator(mode='after')
    def references(self):
        for rows in (self.blocks, self.assets, self.targets):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError('duplicate_rich_ids')
        targets = {t.id for t in self.targets}
        assets = {a.id for a in self.assets}
        segments = []
        for block in self.blocks:
            if block.target_id and block.target_id not in targets:
                raise ValueError('unknown_target')
            if isinstance(block, Narrative):
                segments.extend(block.segment_ids)
            if getattr(block, 'asset_id', None) and block.asset_id not in assets:
                raise ValueError('unknown_asset')
            if isinstance(block, Gallery):
                if any(i.asset_id not in assets or (i.target_id and i.target_id not in targets) for i in block.items):
                    raise ValueError('unknown_gallery_reference')
            if isinstance(block, Table) and any(r.target_id and r.target_id not in targets for r in block.rows):
                raise ValueError('unknown_row_target')
        if len(segments) != len(set(segments)):
            raise ValueError('duplicate_narrative_segment')
        if any(a.target_id not in targets for a in self.assets):
            raise ValueError('unknown_asset_target')
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 65536:
            raise ValueError('rich_response_too_large')
        return self
