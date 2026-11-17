class ProviderError(Exception):
    """The AI could not be reached, or every chain step returned unusable output."""

    def __init__(self, message: str, provider: str, recoverable: bool = True):
        self.message = message
        self.provider = provider
        self.recoverable = recoverable
        super().__init__(f"[{provider}] {message}")


class ResponseShapeError(Exception):
    """A reply parsed as JSON but does not match the requested schema.

    Raised inside the chain loop only: it fails that step and the chain moves on.
    """


class TruncatedResponseError(Exception):
    """The provider stopped at the token ceiling, so the JSON is incomplete."""

    def __init__(self, message: str, raw: str = ""):
        self.raw = raw
        super().__init__(message)
