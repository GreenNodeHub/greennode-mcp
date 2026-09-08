"""Load balancer progress-status vocabulary.

Almost every vLB mutation answers ``202 Accepted`` (or ``200`` with no body) and
the work completes asynchronously, so an agent must poll before it writes again
— the gateway rejects a second mutation while a load balancer is still
settling.

The OpenAPI spec declares **no status enums**: ``displayStatus`` and
``progressStatus`` are plain strings on every DTO. The set below is therefore a
best-effort vocabulary, and :func:`is_settled` deliberately **fails open** — an
unrecognised status counts as settled and the raw value is surfaced to the
caller. Blocking an agent forever on a status nobody anticipated is worse than
letting one write start a few seconds early.
"""

from __future__ import annotations


IN_FLIGHT = frozenset(
    {
        "CREATING",
        "UPDATING",
        "DELETING",
        "PENDING_CREATE",
        "PENDING_UPDATE",
        "PENDING_DELETE",
        "SCALING",
        "RESIZING",
        "REBALANCING",
        "IN_PROGRESS",
        "PROCESSING",
    }
)


def is_settled(*statuses: str | None) -> bool:
    """True when none of *statuses* names an operation still in flight.

    Unknown values are treated as settled (fail open) — see the module
    docstring.
    """
    return not any(
        isinstance(s, str) and s.strip().upper().replace("-", "_") in IN_FLIGHT for s in statuses
    )
