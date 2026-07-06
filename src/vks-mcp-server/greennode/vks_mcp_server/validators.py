"""Input validation utilities for GreenNode MCP Server."""

from __future__ import annotations

import re


ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]$")


def validate_id(value: str, name: str) -> None:
    """Validate that *value* is a safe resource ID.

    IDs must contain only alphanumeric characters and hyphens, and must
    start and end with an alphanumeric character.

    Raises ``ValueError`` if the ID is invalid.
    """
    if not value or not ID_PATTERN.match(value):
        raise ValueError(
            f"Invalid {name}: '{value}'. Must contain only alphanumeric characters and hyphens."
        )
