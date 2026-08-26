"""Input validation utilities shared by all GreenNode MCP servers."""

from __future__ import annotations

import re


ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]$")

PATH_SEGMENT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-]*$")


def validate_id(value: str, name: str) -> None:
    """Validate that *value* is a safe resource ID.

    IDs must contain only alphanumeric characters and hyphens, and must
    start and end with an alphanumeric character — always call this on ID
    arguments before building URLs (prevents path traversal).

    Raises ``ValueError`` if the ID is invalid.
    """
    if not value or not ID_PATTERN.match(value):
        raise ValueError(
            f"Invalid {name}: '{value}'. Must contain only alphanumeric characters and hyphens."
        )


def validate_path_segment(value: str, name: str) -> None:
    """Validate a free-form value that is embedded in a URL path.

    Looser than :func:`validate_id`: dots and underscores are allowed because
    names the platform itself hands back — tag keys such as ``vng.vpc.id`` —
    use them, and rejecting those would make a valid value unusable. Path
    separators and parent-directory hops are still refused, so the guarantee
    against path traversal holds.

    Raises ``ValueError`` if the value is invalid.
    """
    if not value or ".." in value or not PATH_SEGMENT_PATTERN.match(value):
        raise ValueError(
            f"Invalid {name}: '{value}'. Must contain only alphanumeric characters, "
            "dots, underscores and hyphens."
        )
