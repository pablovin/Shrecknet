"""Safe terminal errors for Companion generation and required source validation."""


class CompanionExecutionError(ValueError):
    """Expose a stable code without persisting provider/source exception content."""

    def __init__(self, code, *, retryable=True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
