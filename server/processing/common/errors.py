"""Exceptions shared by processing stages; applications choose how to expose them."""


class ProcessingError(RuntimeError):
    """A processing stage could not complete its work."""


class ProcessingValidationError(ProcessingError, ValueError):
    """Processing input or generated output violates a contract."""
