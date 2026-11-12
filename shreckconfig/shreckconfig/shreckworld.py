"""Non-secret ShreckWorld runtime configuration published by Shrecknet.

Shrecknet's configuration database is authoritative. This atomic file projection
lets independent services read the selected backend without database access.
"""
from pathlib import Path
from typing import Literal
import os
import tempfile

from pydantic import BaseModel, ConfigDict, model_validator

EmbeddingBackend = Literal["onnx", "torch", "openvino"]
EmbeddingDevice = Literal["cpu", "cuda"]
PdfParser = Literal["docling", "pymupdf"]
ParserDevice = Literal["cpu", "cuda"]
ParserDeviceSetting = Literal["auto", "cpu", "cuda"]
TableFormerMode = Literal["fast", "accurate"]
DEFAULT_EMBEDDING_BACKEND: EmbeddingBackend = "onnx"


class ShreckWorldRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding_backend: EmbeddingBackend = DEFAULT_EMBEDDING_BACKEND
    embedding_device: EmbeddingDevice = "cpu"
    pdf_parser: PdfParser = "docling"
    parser_device: ParserDeviceSetting = "auto"
    parser_table_structure_enabled: bool = True
    parser_table_former_mode: TableFormerMode = "fast"

    @model_validator(mode="after")
    def validate_embedding_device(self) -> "ShreckWorldRuntimeConfig":
        if self.embedding_backend != "torch" and self.embedding_device != "cpu":
            raise ValueError("ONNX and OpenVINO embedding backends require embedding_device=cpu")
        return self


def runtime_config_file() -> Path:
    override = os.environ.get("SHRECKWORLD_RUNTIME_CONFIG_FILE")
    if override:
        return Path(override)
    for root in (Path.cwd(), *Path.cwd().parents):
        if (root / "docker-compose.yml").is_file():
            return root / "shrecknet/databases/service-config/shreckworld.json"
    return Path("/service-config/shreckworld.json")


def read_runtime_config(path: Path) -> ShreckWorldRuntimeConfig | None:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return ShreckWorldRuntimeConfig.model_validate_json(content)


def publish_runtime_config(path: Path, config: ShreckWorldRuntimeConfig) -> None:
    """Replace the projection atomically; never expose a partially written file."""
    content = config.model_dump_json(indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            staged = Path(handle.name)
            handle.write(content)
        staged.chmod(0o644)  # This projection contains no credentials.
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
