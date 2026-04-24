"""LLM task types and model policy."""

from enum import Enum


class LLMTask(str, Enum):
    """Enumeration of LLM task types."""

    DECOMPOSE = "decompose"
    SUBANSWER = "subanswer"
    SYNTHESIS = "synthesis"
    VALIDATION = "validation"
    STYLE = "style"
    ARCHITECT_EXTRACT = "architect_extract"


class ModelPolicy:
    """Policy for mapping LLM tasks to models."""

    def __init__(
        self,
        decompose_model: str = "gpt-4o-mini",
        subanswer_model: str = "gpt-4o-mini",
        synthesis_model: str = "gpt-4o",
        validation_model: str = "gpt-4o-mini",
        style_model: str = "gpt-4o-mini",
        architect_extract_model: str = "gpt-5.4-nano",
    ):
        self.task_to_model = {
            LLMTask.DECOMPOSE: decompose_model,
            LLMTask.SUBANSWER: subanswer_model,
            LLMTask.SYNTHESIS: synthesis_model,
            LLMTask.VALIDATION: validation_model,
            LLMTask.STYLE: style_model,
            LLMTask.ARCHITECT_EXTRACT: architect_extract_model,
        }

    def get_model(self, task: LLMTask) -> str:
        """Get the model name for a given task."""
        return self.task_to_model.get(task, "gpt-4o-mini")
