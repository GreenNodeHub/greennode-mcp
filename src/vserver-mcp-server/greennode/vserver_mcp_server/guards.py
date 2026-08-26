"""Shared guards for mutating vServer tools."""

from __future__ import annotations


WRITE_DISABLED_MESSAGE = (
    "Write operations are disabled on this server. Restart it with --allow-write "
    "to enable create/update/delete and power operations."
)


def require_write(allow_write: bool) -> None:
    """Raise unless the server was started with ``--allow-write``.

    Write tools are only registered in write mode, so this is a second line of
    defence for direct handler calls (tests, programmatic use) rather than the
    primary gate.
    """
    if not allow_write:
        raise ValueError(WRITE_DISABLED_MESSAGE)
