"""Load balancer progress-status vocabulary.

Almost every vLB mutation answers ``202 Accepted`` (or ``200`` with no body) and
the work completes asynchronously, so an agent must poll before it writes again
— the gateway rejects a second mutation while a load balancer is still
settling.

The OpenAPI spec declares **no status enums** — ``displayStatus`` and
``progressStatus`` are plain strings on every DTO — so this vocabulary is
transcribed from the gateway source instead: ``LBGatewayStatus.LoadBalancer``
in vlb-gateway (``helper/LBGatewayStatus.java``), cross-checked against live
responses from HCM-3.

Settled states are ``CREATED``, ``ACTIVE``, ``ERROR`` and ``DELETED``;
everything else the gateway can report is a transition. ``is_settled``
deliberately **fails open** — an unrecognised status counts as settled and the
raw value is surfaced to the caller. Blocking an agent forever on a status
nobody anticipated is worse than letting one write start a few seconds early.
"""

from __future__ import annotations


IN_FLIGHT = frozenset(
    {
        # LBGatewayStatus.LoadBalancer — every value that is not CREATED,
        # ACTIVE, ERROR or DELETED. Hyphens are normalised to underscores by
        # is_settled, so CREATING-BILLING is matched as CREATING_BILLING.
        "CREATING",
        "CREATING_BILLING",
        "UPDATING",
        "DELETING",
        "MIGRATING",
        "PENDING_CREATE",
        "PENDING_UPDATE",
        "SCALING",
        "SCALING_IN",
        "SCALING_OUT",
        "REBALANCING",
        # The gateway reports UNDETERMINED while it cannot read back the real
        # state. Treating it as in-flight makes an agent poll again rather than
        # write into a load balancer whose state nobody knows.
        "UNDETERMINED",
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
