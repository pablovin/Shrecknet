"""Read ontology identity and authorization from its authoritative owner."""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from fastapi import Depends, HTTPException, Path
from app.api.deps import get_current_user, oauth2_scheme
from app.core.config import get_settings
from app.repositories.source_scope import OntologyScope
from app.schemas.ontology_sources import OntologyIdentity
from pydantic import ValidationError


def shrecknet_read(path: str, token: str) -> object:
    # JWKS already defines the trusted Shrecknet origin and optional API prefix.
    base = get_settings().shrecknet_jwks_url.rsplit("/auth/", 1)[0]
    try:
        with urlopen(Request(base + path, headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        code = exc.code if exc.code in {401, 403, 404} else 503
        raise HTTPException(code, "Ontology access is unavailable") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Shrecknet ontology service is unavailable") from exc


def resolve_ontology(ontology_id: int, token: str) -> OntologyScope:
    try:
        value = OntologyIdentity.model_validate(shrecknet_read(f"/ontologies/{ontology_id}", token))
    except ValidationError as exc:
        raise HTTPException(503, "Invalid Shrecknet ontology response") from exc
    if value.id != ontology_id:
        raise HTTPException(503, "Invalid Shrecknet ontology response")
    return OntologyScope(ontology_id, value.name, value.description)



def authorized_ontology(ontology_id: int = Path(gt=0), token: str = Depends(oauth2_scheme),
                        _principal=Depends(get_current_user)) -> OntologyScope:
    return resolve_ontology(ontology_id, token)
