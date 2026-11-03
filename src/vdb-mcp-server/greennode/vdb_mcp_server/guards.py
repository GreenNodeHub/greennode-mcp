"""Shared guards for mutating vDB tools."""

from __future__ import annotations


WRITE_DISABLED_MESSAGE = (
    "Write operations are disabled on this server. Restart it with --allow-write "
    "to enable creating, updating, restoring and deleting database resources."
)


def require_write(allow_write: bool) -> None:
    """Raise unless the server was started with ``--allow-write``.

    Write tools are only registered in write mode, so this is a second line of
    defence for direct handler calls (tests, programmatic use) rather than the
    primary gate.
    """
    if not allow_write:
        raise ValueError(WRITE_DISABLED_MESSAGE)


SENSITIVE_ACCESS_DISABLED_MESSAGE = (
    "This tool returns credential material and is disabled on this server. Restart it with "
    "--allow-sensitive-data-access to enable it."
)


def require_sensitive_access(allow_sensitive_data_access: bool) -> None:
    """Raise unless the server was started with ``--allow-sensitive-data-access``.

    Separate from :func:`require_write` because the risk is different: reading
    a Kafka user's credential archive changes nothing on the platform, but it
    puts private keys and their passwords on the local disk. A server may
    reasonably allow writes and still refuse that.
    """
    if not allow_sensitive_data_access:
        raise ValueError(SENSITIVE_ACCESS_DISABLED_MESSAGE)
