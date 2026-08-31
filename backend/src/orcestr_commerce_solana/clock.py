from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provides aware UTC time to domain services and tests."""

    def now(self) -> datetime:
        """Returns the current timezone-aware UTC timestamp."""
        ...


class SystemClock:
    """Reads production time from the standard UTC clock."""

    def now(self) -> datetime:
        """Returns the current timezone-aware UTC timestamp."""
        return datetime.now(UTC)


class FrozenClock:
    """Provides a deterministic timestamp for tests and offline tools."""

    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Frozen clock value must be timezone-aware.")
        self.value = value.astimezone(UTC)

    def now(self) -> datetime:
        """Returns the configured UTC timestamp."""
        return self.value


def utc_datetime(value: datetime, field_name: str = "Timestamp") -> datetime:
    """Rejects naive datetimes and normalizes accepted values to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def database_utc_datetime(value: datetime) -> datetime:
    """Restores UTC tzinfo stripped by SQLite from a timezone-aware DB column."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
