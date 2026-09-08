"""Input validation for vLB path parameters.

Extends the shared ``mcp_core`` validators with a vLB-specific guard: several
**static** segments live under ``/loadBalancers/`` (``packages``,
``all_packages``, ``headers``, ``subnet``) and collide with
``/loadBalancers/{loadBalancerId}``. ``validate_id`` accepts every one of them
as a plausible id, so without the extra check a call like
``get_load_balancer(load_balancer_id="packages")`` quietly fetches the package
catalogue and parses it as an empty load balancer instead of failing.
"""

from __future__ import annotations

from greennode.mcp_core.validators import ID_PATTERN, validate_id, validate_path_segment


__all__ = [
    "ID_PATTERN",
    "RESERVED_LB_SEGMENTS",
    "validate_id",
    "validate_lb_id",
    "validate_path_segment",
]


RESERVED_LB_SEGMENTS = frozenset({"packages", "all_packages", "headers", "subnet"})


def validate_lb_id(value: str, name: str = "load_balancer_id") -> None:
    """Validate a load balancer id, refusing the reserved static path segments.

    The error names the tool that actually serves each reserved word, so an
    agent that guessed wrong is routed instead of merely blocked. The reserved
    check runs FIRST: ``all_packages`` would otherwise be rejected by
    ``validate_id`` for its underscore, with a message about allowed characters
    that leaves the agent no better off.
    """
    if value.lower() in RESERVED_LB_SEGMENTS:
        raise ValueError(
            f"Invalid {name}: '{value}' is a reserved VLB path segment, not a load "
            "balancer id. Use list_load_balancer_packages, list_listener_headers "
            "or list_load_balancers_by_subnet instead."
        )
    validate_id(value, name)
