"""LLM task types and model policy."""

from enum import Enum

from app.core.config_store import LLMModelTarget

class LLMTask(str, Enum):
    """Enumeration of LLM task types."""

    DECOMPOSE = "decompose"
    SYNTHESIS = "synthesis"
    ARCHITECT_EXTRACT = "architect_extract"


class ModelPolicy:
    """Policy for mapping LLM tasks to models."""

    def __init__(
        self,
        default_model: LLMModelTarget | str | None = None,
        architect_extract_model: LLMModelTarget | str | None = None,
    ):
        self.default_model = self._coerce_target(default_model or "gpt-5-nano")
        self.task_to_model = {
            LLMTask.DECOMPOSE: self.default_model,
            LLMTask.SYNTHESIS: self.default_model,
            LLMTask.ARCHITECT_EXTRACT: self._coerce_target(
                architect_extract_model or "gpt-5.4-nano"
            ),
        }

    def _coerce_target(self, value: LLMModelTarget | str | dict[str, str]) -> LLMModelTarget:
        if isinstance(value, LLMModelTarget):
            return value
        if isinstance(value, str):
            return LLMModelTarget(provider="openai", name=value.strip() or "gpt-5-nano")
        provider = str(value.get("provider") or "openai").strip() or "openai"
        name = str(value.get("name") or "gpt-5-nano").strip() or "gpt-5-nano"
        return LLMModelTarget(provider=provider, name=name)

    def get_model(self, task: LLMTask) -> LLMModelTarget:
        """Get the provider+model target for a given task."""
        return self.task_to_model.get(task, self.default_model)
