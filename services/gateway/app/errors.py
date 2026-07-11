"""Gateway error types, mapped to HTTP responses in routes.py."""


class ForgeError(Exception):
    status_code = 500
    error_type = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthError(ForgeError):
    status_code = 401
    error_type = "invalid_api_key"


class QuotaExceeded(ForgeError):
    status_code = 429
    error_type = "quota_exhausted"


class QueueFull(ForgeError):
    status_code = 429
    error_type = "queue_full"


class QueueWaitTimeout(ForgeError):
    status_code = 503
    error_type = "queue_wait_timeout"


class AllBackendsFailed(ForgeError):
    status_code = 502
    error_type = "all_backends_failed"
