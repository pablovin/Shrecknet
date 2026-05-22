from __future__ import annotations


class ShrecknetError(Exception):
    pass


class ShrecknetHTTPError(ShrecknetError):
    def __init__(self, status_code: int, detail: str | None = None):
        self.status_code = status_code
        self.detail = detail or "HTTP error"
        super().__init__(f"{status_code}: {self.detail}")


class AuthenticationError(ShrecknetHTTPError):
    pass


class AuthorizationError(ShrecknetHTTPError):
    pass


class NotFoundError(ShrecknetHTTPError):
    pass


class ConflictError(ShrecknetHTTPError):
    pass


class ValidationError(ShrecknetHTTPError):
    pass


class ConfigurationReadinessError(ShrecknetError):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons) if reasons else "Configuration is not ready")


def raise_for_status(status_code: int, detail: str | None) -> None:
    if status_code < 400:
        return
    if status_code == 401:
        raise AuthenticationError(status_code, detail)
    if status_code == 403:
        raise AuthorizationError(status_code, detail)
    if status_code == 404:
        raise NotFoundError(status_code, detail)
    if status_code == 409:
        raise ConflictError(status_code, detail)
    if status_code == 422:
        raise ValidationError(status_code, detail)
    raise ShrecknetHTTPError(status_code, detail)
