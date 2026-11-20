"""HTTP client for the vLB API (built on greennode.mcp_core)."""

from __future__ import annotations

import re
from greennode.mcp_core.http import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRYABLE_STATUS_CODES,
    BaseClient,
)
from greennode.vlb_mcp_server.auth import TokenManager
from greennode.vlb_mcp_server.config import VLB_SERVICE, VSERVER_SERVICE, VlbConfig
from greennode.vlb_mcp_server.status import IN_FLIGHT
from greennode.vlb_mcp_server.useragent import USER_AGENT
from typing import Any


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY",
    "RETRYABLE_STATUS_CODES",
    "VlbClient",
]


#: The only three ``errorCode`` values the gateway emits
#: (``helper/ErrorCodeConstants.java``). All three arrive as HTTP 400, so
#: without this mapping they read as a malformed request the agent should fix,
#: when in fact the body was fine and the resource was merely busy.
NOT_READY_CODES = {
    "LoadBalancerNotReady": "load balancer",
    "ListenerNotReady": "listener",
    "PoolNotReady": "pool",
}

#: Phrases the gateway uses for a conflict that will clear on its own, all of
#: them 400. ``in-process``/``timeout`` are a Redis lock it could not take;
#: ``not ready`` and ``already in progress`` are an operation still running.
_TRANSIENT_PHRASES = (
    "is in-process",
    "is timeout",
    "is not ready",
    "an operation is already in progress",
)

#: Eight validators build their rejection as
#: ``String.format("<Resource> id %s is %s", uuid, status.toLowerCase())``, so
#: the message ends in the resource's own status. Matching the shape rather
#: than a phrase list covers all of them, and stays right if the gateway adds
#: another status.
_TRAILING_STATUS = re.compile(r"\bis ([a-z][a-z_-]*)\.?$")

_POLL_HINT = (
    "This is a transient conflict, not a bad request — the body was accepted. "
    "Poll get_load_balancer_status until `settled` is true, then retry the same call."
)


def _transient_reason(message: str) -> str | None:
    """Why this 400 will clear on its own, or None if it will not.

    Deliberately narrow. ``"Load Balancer <id> is expired"`` and
    ``"Load balancer id <id> is not a leader"`` are the same grammatical shape
    and are **terminal** — the first needs billing, the second needs a
    different load balancer — so only a status the gateway itself reports as
    in flight counts.
    """
    lowered = message.lower()
    for phrase in _TRANSIENT_PHRASES:
        if phrase in lowered:
            return "an earlier operation is still running"
    match = _TRAILING_STATUS.search(lowered)
    if match and match.group(1).upper().replace("-", "_") in IN_FLIGHT:
        return f"it is currently {match.group(1)}"
    return None


class VlbClient(BaseClient):
    """Async client for the vLB API (retry + token refresh from BaseClient).

    Most mutations answer ``202 Accepted`` (deletes, rebalancing) or ``200`` with
    no content (listener/pool/member/policy updates), which ``BaseClient``
    surfaces as ``None`` — callers must never index into the result of a write.
    """

    def __init__(self, config: VlbConfig, token_manager: TokenManager) -> None:
        super().__init__(config, token_manager, default_service=VLB_SERVICE, user_agent=USER_AGENT)

    def _raise_error(self, resp) -> None:
        """Relabel vLB's "busy" 400s before falling back to the shared handler.

        The gateway answers a resource that is still settling with
        ``400 {"message": ..., "errorCode": "LoadBalancerNotReady"}`` and a lost
        lock with ``400 "LOAD-BALANCER <id> is in-process"``. ``BaseClient``
        renders both as ``Bad request: ...``, which tells an agent to change the
        body — the one thing that cannot help. Quota exhaustion is also a 400
        here (not a 409, unlike other GreenNode services), and that one really
        is terminal, so it deliberately keeps the generic wording.
        """
        if resp.status_code == 400:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001 — a non-JSON body just means no hint
                body = None
            if isinstance(body, dict):
                message = str(body.get("message") or "")
                resource = NOT_READY_CODES.get(str(body.get("errorCode") or ""))
                if resource:
                    raise RuntimeError(
                        f"The {resource} is still processing an earlier change: "
                        f"{message or 'not ready'}. {_POLL_HINT}"
                    )
                reason = _transient_reason(message)
                if reason:
                    raise RuntimeError(
                        f"The resource cannot accept this change yet — {reason}: "
                        f"{message}. {_POLL_HINT}"
                    )
        super()._raise_error(resp)

    async def get_vserver(
        self, path: str, region: str | None = None, params: dict | None = None
    ) -> Any:
        """GET against the **vServer** gateway instead of vLB.

        The one place this package leaves its own product, and it is not
        optional: vLB exposes no project-listing endpoint, so even resolving
        ``/v2/{projectId}/...`` goes through here. The same gateway answers the
        zone, VPC, subnet and server lookups a placement decision needs.

        Kept to reads: this package must never mutate a vServer resource. That
        belongs to ``vserver-mcp-server``.
        """
        return await self._request(
            "GET", path, region=region, params=params, service=VSERVER_SERVICE
        )
