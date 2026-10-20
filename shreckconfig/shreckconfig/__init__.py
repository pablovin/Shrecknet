"""CORS policy shared by independently deployed Shrecknet services.

Read dotenv values directly, without environment merging or variable interpolation.
The container mount is fixed; local development uses the repository-root .env.
"""
from pathlib import Path
import json
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator

CORS_FIELDS = frozenset({
    "cors_allow_origins", "cors_allow_origin_regex", "cors_allow_credentials",
    "cors_allow_methods", "cors_allow_headers", "cors_max_age",
})


class CorsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allow_origins: list[str] = Field(default_factory=list)
    allow_origin_regex: str | None = None
    allow_credentials: bool = False
    allow_methods: list[str] = Field(default_factory=list)
    allow_headers: list[str] = Field(default_factory=list)
    max_age: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_origins(self):
        if self.allow_origin_regex:
            re.compile(self.allow_origin_regex)
        for origin in self.allow_origins:
            parsed = urlsplit(origin)
            if origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths or credentials")
        return self


def cors_file() -> Path:
    mounted = Path("/configs/browser.env")
    if mounted.exists():
        return mounted
    for root in (Path.cwd(), *Path.cwd().parents):
        if (root / "docker-compose.yml").is_file():
            return root / ".env"
    return mounted


def load_cors_policy(path: Path | None = None) -> CorsPolicy:
    path = path if path is not None else cors_file()
    # Missing files deny cross-origin access instead of enabling hidden defaults.
    if not path.is_file():
        return CorsPolicy()
    values = dotenv_values(path, interpolate=False)
    policy = {}
    for name in CorsPolicy.model_fields:
        key = f"CORS_{name.upper()}"
        if key not in values:
            continue
        value = values[key]
        try:
            policy[name] = (value or None) if name == "allow_origin_regex" else json.loads(value or "")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid {key} in CORS .env file") from exc
    return CorsPolicy.model_validate(policy)
