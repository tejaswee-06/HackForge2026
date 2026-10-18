from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 degraded_mode: bool = False, provider: Optional[str] = None, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable, self.degraded_mode, self.provider, self.details = retryable, degraded_mode, provider, details


# provider error code -> HTTP status
PROVIDER_HTTP = {
    "PROVIDER_NOT_CONFIGURED": 503, "PROVIDER_RATE_LIMITED": 503, "PROVIDER_UNAVAILABLE": 503,
    "PROVIDER_UNREACHABLE": 503, "PROVIDER_RETRY_EXHAUSTED": 503, "PROVIDER_TIMEOUT": 504,
    "PROVIDER_AUTH": 502, "PROVIDER_BAD_RESPONSE": 502, "UNSUPPORTED_CHAIN": 400,
}
