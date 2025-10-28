from __future__ import annotations

import re
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import (
    get_current_user,
    get_media_service,
    get_notification_service,
    get_optional_ontology_instance_service,
    get_ontology_service,
    get_user_service,
    require_roles,
)
from app.core.config import get_settings
from app.models.user import User, UserRole
from app.services.media_service import ImageValidationError, MediaService
from app.services.notification_service import NotificationService
from app.services.ontology_instance_service import OntologyInstanceService
from app.services.ontology_service import OntologyService
from app.services.user_service import UserService

router = APIRouter(
    prefix="/media-admin",
    tags=["media"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)

settings = get_settings()

_COMPONENT_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


async def _ensure_user_exists(user_service: UserService, instance_id: str) -> None:
    try:
        user_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'user'",
        ) from exc
    user = await user_service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )


async def _ensure_ontology_exists(
    ontology_service: OntologyService, instance_id: str
) -> None:
    try:
        ontology_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'ontology'",
        ) from exc
    ontology = await ontology_service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )


async def _ensure_entity_exists(
    ontology_service: OntologyService, instance_id: str
) -> None:
    try:
        entity_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'entity'",
        ) from exc
    entity = await ontology_service.get_entity_by_id(entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology entity not found"
        )


async def _ensure_notification_exists(
    notification_service: NotificationService, instance_id: str
) -> None:
    try:
        notification_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'notification'",
        ) from exc
    notification = await notification_service.get_notification(notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )


async def _ensure_property_exists(
    ontology_service: OntologyService, instance_id: str
) -> None:
    try:
        property_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'property'",
        ) from exc
    prop = await ontology_service.get_property_by_id(property_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology property not found"
        )


async def _ensure_ontology_instance_exists(
    ontology_instance_service: OntologyInstanceService, instance_id: str
) -> None:
    try:
        await ontology_instance_service.get_instance(instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology instance not found"
        ) from exc


async def _ensure_relationship_exists(
    ontology_service: OntologyService, instance_id: str
) -> None:
    try:
        relationship_id = int(instance_id)
    except ValueError as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_id must be an integer for model 'relationship'",
        ) from exc
    relationship = await ontology_service.get_relationship_by_id(relationship_id)
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ontology relationship not found",
        )


def _sanitize_component(value: str, *, field: str, to_lower: bool = False) -> str:
    cleaned = value.strip()
    if to_lower:
        cleaned = cleaned.lower()
    cleaned = cleaned.replace("..", "").replace("/", "")
    if not cleaned or not _COMPONENT_RE.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field}",
        )
    return cleaned


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    model: str = Form(...),
    instance_id: str = Form(...),
    media_service: MediaService = Depends(get_media_service),
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
    ontology_service: OntologyService = Depends(get_ontology_service),
    notification_service: NotificationService = Depends(get_notification_service),
    ontology_instance_service: OntologyInstanceService
    | None = Depends(get_optional_ontology_instance_service),
) -> dict[str, str]:
    model_key = _sanitize_component(model, field="model", to_lower=True)

    validators: dict[str, Callable[[str], Awaitable[None]]] = {
        "user": lambda instance: _ensure_user_exists(user_service, instance),
        "ontology": lambda instance: _ensure_ontology_exists(
            ontology_service, instance
        ),
        "entity": lambda instance: _ensure_entity_exists(ontology_service, instance),
        "property": lambda instance: _ensure_property_exists(
            ontology_service, instance
        ),
        "relationship": lambda instance: _ensure_relationship_exists(
            ontology_service, instance
        ),
        "notification": lambda instance: _ensure_notification_exists(
            notification_service, instance
        ),
    }
    if ontology_instance_service is not None:
        validators[
            "ontology_instance"
        ] = lambda instance: _ensure_ontology_instance_exists(
            ontology_instance_service, instance
        )

    validator = validators.get(model_key)
    if validator is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Unsupported model for upload"
        )

    await validator(instance_id)

    safe_instance_id = _sanitize_component(instance_id, field="instance_id")
    category_path = f"{model_key}/{safe_instance_id}"

    try:
        url = await media_service.save_image(
            file,
            category=category_path,
            identifier=f"{model_key}_{safe_instance_id}",
            resize=(settings.image_max_width, settings.image_max_height),
            filename="image_url.png",
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc

    return {"url": url}
