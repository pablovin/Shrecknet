"""Domain errors for shreckLLM."""


class ShreckLLMError(RuntimeError):
    """Base error type."""


class DependencyUnavailableError(ShreckLLMError):
    """Raised when a downstream dependency is not available."""


class InvalidModelError(ShreckLLMError):
    """Raised when the configured/requested model does not exist."""


class ProviderTimeoutError(ShreckLLMError):
    """Raised when provider calls time out."""


class ProviderOverloadedError(ShreckLLMError):
    """Raised when local service is overloaded."""


class ProviderAuthenticationError(ShreckLLMError):
    """Raised when provider credentials are missing/invalid."""


class ProviderPermissionError(ShreckLLMError):
    """Raised when provider denies access due to permissions."""


class ProviderBadRequestError(ShreckLLMError):
    """Raised when provider rejects request as invalid."""
