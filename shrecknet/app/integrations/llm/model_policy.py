"""LLM task types and model policy."""

from enum import Enum


class LLMTask(str, Enum):
    """Enumeration of LLM task types."""

    DECOMPOSE = "decompose"
    SYNTHESIS = "synthesis"
    ARCHITECT_EXTRACT = "architect_extract"


class ModelPolicy:
    """Policy for mapping LLM tasks to models."""

    def __init__(
        self,
        default_model: str = "gpt-5-nano",
        architect_extract_model: str = "gpt-5.4-nano",
    ):
        self.default_model = default_model
        self.task_to_model = {
            LLMTask.DECOMPOSE: default_model,
            LLMTask.SYNTHESIS: default_model,
            LLMTask.ARCHITECT_EXTRACT: architect_extract_model,
        }

    def get_model(self, task: LLMTask) -> str:
        """Get the model name for a given task."""
        return self.task_to_model.get(task, self.default_model)
