"""Errors raised while talking to a data source."""

from __future__ import annotations


class FetchError(RuntimeError):
    """A request to a data source failed or returned an unusable response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
