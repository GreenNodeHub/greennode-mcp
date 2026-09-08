"""Write DTOs — the request bodies the vLB API accepts.

All fields are **camelCase**, matching the wire format exactly, and every model
sets ``extra="forbid"`` so a misspelled field fails locally with a field name
instead of remotely with an opaque Java validation message.

Two spec quirks are corrected rather than mirrored, both documented at the
model:

- ``allowedCidrs`` / ``blockedCidrs`` are typed ``string`` but hold a list;
  the DTO accepts either and serialises to the single string.
- ``LbPool.members`` is typed as a single object while ``CreatePoolRequest``
  types the same thing as an array. It is an array.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import TagDto, list_to_csv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal


NAME_PATTERN = r"^[a-zA-Z0-9_\-.]{5,50}$"

DESCRIPTION_CIDRS = (
    "CIDRs as a list (e.g. ['10.0.0.0/8']) or one comma-separated string. "
    "Sent to the API as a single comma-separated string."
)


class HeaderDto(BaseModel):
    """A header a listener inserts into every forwarded request."""

    model_config = ConfigDict(extra="forbid")

    headerName: str = Field(..., description="Header name — must be one of list_listener_headers.")
    headerValue: str = Field(..., description="Header value.")


class MemberDto(BaseModel):
    """One backend member of a pool.

    ``backup`` is **required** by the API even when false, so it carries a
    default here and is always serialised — never dropped by ``exclude_none``.
    """

    model_config = ConfigDict(extra="forbid")

    ipAddress: str = Field(
        ...,
        description="Private IPv4 of the backend server (list_servers → private_ip).",
    )
    port: int = Field(..., ge=1, le=65535, description="Port the backend serves on.")
    backup: bool = Field(
        False, description="Whether this member only receives traffic when others are down."
    )
    weight: int | None = Field(
        None, ge=1, description="Relative share of requests; higher gets more."
    )
    name: str | None = Field(None, pattern=NAME_PATTERN, description="Friendly name (5-50 chars).")
    monitorPort: int | None = Field(
        None, ge=1, le=65535, description="Port the health monitor probes, if different."
    )


class CreateHealthMonitorDto(BaseModel):
    """Health monitor created together with a pool.

    There is no standalone health-monitor endpoint: this is the only way to
    create one, and ``healthCheckProtocol`` cannot be changed afterwards —
    ``UpdateHealthMonitorDto`` has no such field.
    """

    model_config = ConfigDict(extra="forbid")

    healthCheckProtocol: Literal["TCP", "HTTP", "HTTPS", "PING-UDP"] = Field(
        ..., description="Probe protocol. Immutable once the pool exists."
    )
    healthCheckPath: str | None = Field(
        None, description="Path to probe (HTTP/HTTPS only), e.g. '/healthz'."
    )
    healthCheckMethod: Literal["GET", "POST", "PUT"] | None = Field(
        None, description="HTTP method for the probe (HTTP/HTTPS only)."
    )
    healthyThreshold: int | None = Field(
        None, ge=2, le=10, description="Consecutive successes before a member is healthy."
    )
    unhealthyThreshold: int | None = Field(
        None, ge=2, le=10, description="Consecutive failures before a member is unhealthy."
    )
    timeout: int | None = Field(None, ge=2, le=120, description="Probe timeout in seconds.")
    interval: int | None = Field(None, ge=5, le=3600, description="Probe interval in seconds.")
    successCode: str | None = Field(
        None, description="Accepted HTTP status codes, e.g. '200' or '200-299'."
    )
    domainName: str | None = Field(None, description="Host header for the probe.")
    httpVersion: Literal["1.0", "1.1"] | None = Field(
        None, description="HTTP version to probe with."
    )


class _ListenerFieldsDto(BaseModel):
    """Listener settings shared by the embedded and standalone create bodies."""

    model_config = ConfigDict(extra="forbid")

    timeoutClient: int | None = Field(
        None, ge=1, le=3600, description="Client idle timeout in seconds."
    )
    timeoutMember: int | None = Field(
        None, ge=1, le=3600, description="Member idle timeout in seconds."
    )
    timeoutConnection: int | None = Field(
        None, ge=1, le=3600, description="Connection idle timeout in seconds."
    )
    allowedCidrs: str | None = Field(None, description=f"Allowed source {DESCRIPTION_CIDRS}")
    blockedCidrs: str | None = Field(None, description=f"Blocked source {DESCRIPTION_CIDRS}")
    defaultAction: Literal["accept", "drop"] | None = Field(
        None, description="What to do with traffic matching no L7 policy."
    )
    certificateAuthorities: list[str] | None = Field(
        None, description="Certificate ids offered for SNI (HTTPS only)."
    )
    defaultCertificateAuthority: str | None = Field(
        None, description="Certificate served when SNI matches nothing. Required for HTTPS."
    )
    clientCertificate: str | None = Field(
        None, description="Certificate id used to verify client certificates (mTLS)."
    )
    insertHeaders: list[HeaderDto] | None = Field(
        None, description="Headers to inject into forwarded requests."
    )
    alpnProtocols: list[Literal["http/1.1", "http/1.0", "h2"]] | None = Field(
        None, description="ALPN protocols advertised during the TLS handshake."
    )
    tlsSecurityPolicy: Literal["COMPATIBLE", "MODERN", "STRICT"] | None = Field(
        None, description="Cipher/protocol policy for TLS."
    )

    @field_validator("allowedCidrs", "blockedCidrs", mode="before")
    @classmethod
    def _join_cidrs(cls, value):
        """Accept a list of CIDRs and serialise to the single string the API expects."""
        if value is None:
            return None
        return list_to_csv(value)


class EmbeddedListenerDto(_ListenerFieldsDto):
    """Listener created inline with a load balancer (``CreateLoadBalancerRequest.listener``).

    Unlike the standalone create, the three timeouts are optional here.
    """

    listenerName: str = Field(..., pattern=NAME_PATTERN, description="Listener name (5-50 chars).")
    listenerProtocol: Literal["HTTP", "HTTPS", "TCP", "UDP"] = Field(
        ..., description="Listener protocol. HTTP/HTTPS require a Layer 7 load balancer."
    )
    listenerProtocolPort: int | None = Field(
        None, ge=1, le=65535, description="Port the listener binds to."
    )


class EmbeddedPoolDto(BaseModel):
    """Pool created inline with a load balancer (``CreateLoadBalancerRequest.pool``)."""

    model_config = ConfigDict(extra="forbid")

    poolName: str = Field(..., pattern=NAME_PATTERN, description="Pool name (5-50 chars).")
    algorithm: Literal["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"] = Field(
        ..., description="Load balancing algorithm."
    )
    poolProtocol: Literal["HTTP", "TCP", "UDP", "PROXY"] = Field(
        ..., description="Protocol used to reach the backends."
    )
    stickiness: bool | None = Field(None, description="Pin a client to one backend.")
    tlsEncryption: bool | None = Field(None, description="Encrypt traffic to the backends.")
    healthMonitor: CreateHealthMonitorDto | None = Field(
        None, description="Health monitor for the pool."
    )
    members: list[MemberDto] | None = Field(
        None,
        description=(
            "Backend members. The OpenAPI spec types this as a single object — that is a "
            "spec bug; a pool takes many members and this is sent as an array."
        ),
    )


class CreateLoadBalancerDto(BaseModel):
    """Request body for ``POST /v2/{projectId}/loadBalancers``.

    A load balancer cannot be renamed and its ``type`` cannot be changed, so
    both are effectively permanent once this succeeds.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        pattern=NAME_PATTERN,
        description="Load balancer name (5-50 chars, letters/digits/_-.). Cannot be changed later.",
    )
    packageId: str = Field(..., description="Package id from list_load_balancer_packages.")
    scheme: Literal["Internet", "Internal"] = Field(
        ...,
        description="'Internet' gets a public address; 'Internal' is reachable only inside the VPC.",
    )
    subnetId: str = Field(..., description="Subnet id from list_subnets.")
    type: Literal["Layer 4", "Layer 7"] = Field(
        ...,
        description=(
            "'Layer 4' for TCP/UDP, 'Layer 7' for HTTP/HTTPS and L7 routing policies. "
            "Note the space in both values."
        ),
    )
    zoneId: str | None = Field(None, description="Availability zone, e.g. 'HCM03-1A'.")
    autoScalable: bool | None = Field(
        None,
        description=(
            "Enable autoscaling. Min/max node counts are set afterwards with "
            "configure_load_balancer_scaling."
        ),
    )
    tags: list[TagDto] | None = Field(None, description="Key/value tags.")
    listener: EmbeddedListenerDto | None = Field(
        None, description="Optional listener created together with the load balancer."
    )
    pool: EmbeddedPoolDto | None = Field(
        None, description="Optional pool created together with the load balancer."
    )


class ResizeLoadBalancerDto(BaseModel):
    """Request body for ``PUT /v2/{projectId}/loadBalancers/{id}/resize``."""

    model_config = ConfigDict(extra="forbid")

    packageId: str = Field(..., description="Target package id from list_load_balancer_packages.")


class UpdateBandwidthPackageDto(BaseModel):
    """Request body for ``PUT /v2/{projectId}/loadBalancers/{id}/bandwidth``.

    Note the capital W in ``bandWidthPackageId`` — that is the spelling the API
    declares, and the lowercase form is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    bandWidthPackageId: str = Field(
        ...,
        pattern=r"^bandwidth-pack-[0-9a-f\-]+$",
        description="Bandwidth package id, formatted 'bandwidth-pack-<uuid>'.",
    )


class ScalingConfigDto(BaseModel):
    """Min/max node counts for a Multi-AZ autoscaling load balancer."""

    model_config = ConfigDict(extra="forbid")

    maxSize: int = Field(..., ge=1, description="Maximum number of nodes.")
    minSize: int | None = Field(
        None, ge=1, description="Minimum number of nodes; defaults to the number of subnets."
    )


class NetworkingConfigDto(BaseModel):
    """Subnets a Multi-AZ autoscaling load balancer spans."""

    model_config = ConfigDict(extra="forbid")

    subnets: list[str] = Field(
        ...,
        min_length=1,
        description="All active subnet ids — MUST include the load balancer's primary subnet.",
    )


class ScaleConfigDto(BaseModel):
    """Request body for ``PUT /v2/{projectId}/loadBalancers/{id}/rebalancing``."""

    model_config = ConfigDict(extra="forbid")

    scaling: ScalingConfigDto = Field(..., description="Node count bounds.")
    networking: NetworkingConfigDto = Field(..., description="Subnet topology.")


class CloneCertificateDto(BaseModel):
    """Per-listener certificate override applied to a cloned load balancer."""

    model_config = ConfigDict(extra="forbid")

    listenerId: str = Field(..., description="Listener in the SOURCE load balancer.")
    defaultCertificateId: str = Field(..., description="Certificate the clone's listener serves.")
    sniCertificateIds: list[str] | None = Field(
        None, description="Additional certificates offered for SNI."
    )


class CloneLoadBalancerDto(BaseModel):
    """Request body for ``POST /v2/{projectId}/loadBalancers/{id}/clone``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=NAME_PATTERN, description="Name of the new load balancer.")
    packageId: str = Field(..., description="Package id for the new load balancer.")
    certificates: list[CloneCertificateDto] | None = Field(
        None,
        description=(
            "Certificate overrides per listener. Read the source's listeners from "
            "get_load_balancer_clone_metadata first."
        ),
    )


class MemberTargetDto(BaseModel):
    """Identifies an existing pool member for removal.

    A member has no stable id on the write side — ``ChangeMembersRequest`` only
    ever carries the full desired list — so ``(ipAddress, port)`` is the key.
    """

    model_config = ConfigDict(extra="forbid")

    ipAddress: str = Field(..., description="Private IPv4 of the member to remove.")
    port: int = Field(..., ge=1, le=65535, description="Port of the member to remove.")


class ChangeMembersDto(BaseModel):
    """Request body for ``PUT .../pools/{poolId}/members``.

    Full replacement — whatever is not in ``members`` is removed from the pool.
    """

    model_config = ConfigDict(extra="forbid")

    members: list[MemberDto] = Field(
        ..., description="The COMPLETE desired member list, not a delta."
    )


class CreatePoolDto(BaseModel):
    """Request body for ``POST /v2/{projectId}/loadBalancers/{id}/pools``.

    ``healthMonitor`` is **required**: there is no standalone health-monitor
    endpoint, so a pool created without one can never get one except by
    rewriting the pool.
    """

    model_config = ConfigDict(extra="forbid")

    poolName: str = Field(..., pattern=NAME_PATTERN, description="Pool name (5-50 chars).")
    poolProtocol: Literal["HTTP", "TCP", "UDP", "PROXY"] = Field(
        ..., description="Protocol used to reach the backends. Cannot be changed later."
    )
    algorithm: Literal["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"] = Field(
        ..., description="Load balancing algorithm."
    )
    healthMonitor: CreateHealthMonitorDto = Field(
        ..., description="Health check for the members. Required by the API."
    )
    stickiness: bool | None = Field(None, description="Pin a client to one backend.")
    tlsEncryption: bool | None = Field(None, description="Encrypt traffic to the backends.")
    members: list[MemberDto] | None = Field(
        None, description="Initial backend members; can also be added later."
    )


class UpdateHealthMonitorDto(BaseModel):
    """Health monitor fields accepted by ``PUT .../pools/{poolId}``.

    Note what is missing: ``healthCheckProtocol``. The probe protocol is fixed
    at pool creation and can only be changed by deleting and recreating the
    pool. The four thresholds are **required** by the API, so a partial update
    must resend the pool's current values — which is what
    ``update_pool_health_monitor`` does.
    """

    model_config = ConfigDict(extra="forbid")

    healthyThreshold: int = Field(..., ge=2, le=10, description="Successes before healthy.")
    unhealthyThreshold: int = Field(..., ge=2, le=10, description="Failures before unhealthy.")
    timeout: int = Field(..., ge=2, le=120, description="Probe timeout in seconds.")
    interval: int = Field(..., ge=5, le=3600, description="Probe interval in seconds.")
    healthCheckPath: str | None = Field(None, description="Path to probe (HTTP/HTTPS).")
    healthCheckMethod: Literal["GET", "POST", "PUT"] | None = Field(
        None, description="HTTP method for the probe."
    )
    successCode: str | None = Field(None, description="Accepted HTTP status codes.")
    domainName: str | None = Field(None, description="Host header for the probe.")
    httpVersion: Literal["1.0", "1.1"] | None = Field(
        None, description="HTTP version to probe with."
    )


class UpdatePoolDto(BaseModel):
    """Request body for ``PUT /v2/{projectId}/loadBalancers/{id}/pools/{poolId}``.

    ``algorithm`` is required even when you only meant to change something else,
    so a partial body silently resets the load balancing method. Neither the
    pool name nor its protocol can be changed.
    """

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"] = Field(
        ..., description="Load balancing algorithm. Required; resend the current one to keep it."
    )
    stickiness: bool | None = Field(None, description="Pin a client to one backend.")
    tlsEncryption: bool | None = Field(None, description="Encrypt traffic to the backends.")
    healthMonitor: UpdateHealthMonitorDto | None = Field(
        None,
        description=(
            "Health monitor settings. Omitting this may reset the pool's monitor — "
            "prefer update_pool_health_monitor, which carries the current values forward."
        ),
    )


class CreateListenerDto(_ListenerFieldsDto):
    """Request body for ``POST /v2/{projectId}/loadBalancers/{id}/listeners``.

    The three timeouts are **required** here, unlike in
    :class:`EmbeddedListenerDto` where the same listener is created inline with
    the load balancer.
    """

    listenerName: str = Field(..., pattern=NAME_PATTERN, description="Listener name (5-50 chars).")
    listenerProtocol: Literal["HTTP", "HTTPS", "TCP", "UDP"] = Field(
        ...,
        description=(
            "Listener protocol. HTTP/HTTPS require a 'Layer 7' load balancer; "
            "TCP/UDP require 'Layer 4'."
        ),
    )
    listenerProtocolPort: int = Field(
        ..., ge=1, le=65535, description="Port the listener binds to."
    )
    timeoutClient: int = Field(..., ge=1, le=3600, description="Client idle timeout (seconds).")
    timeoutMember: int = Field(..., ge=1, le=3600, description="Member idle timeout (seconds).")
    timeoutConnection: int = Field(
        ..., ge=1, le=3600, description="Connection idle timeout (seconds)."
    )
    defaultPoolId: str | None = Field(
        None, description="Pool this listener forwards to by default (create the pool first)."
    )


class UpdateListenerDto(_ListenerFieldsDto):
    """Request body for ``PUT .../listeners/{listenerId}``.

    A full replacement, not a patch: the three timeouts are required, and
    omitting ``certificateAuthorities`` or ``insertHeaders`` may clear them.
    Read the listener with ``get_listener`` and resend what you want to keep.
    Name, protocol and port cannot be changed.
    """

    timeoutClient: int = Field(..., ge=1, le=3600, description="Client idle timeout (seconds).")
    timeoutMember: int = Field(..., ge=1, le=3600, description="Member idle timeout (seconds).")
    timeoutConnection: int = Field(
        ..., ge=1, le=3600, description="Connection idle timeout (seconds)."
    )
    defaultPoolId: str | None = Field(
        None, description="Pool this listener forwards to by default."
    )


class ImportCertificateDto(BaseModel):
    """Request body for ``POST /v2/{projectId}/cas``.

    Two spellings to watch: the ``type`` enum value is literally ``TLS/SSL``
    (with the slash), and the name pattern here — unlike every other name in
    this API — allows only letters, digits, dots and hyphens, **no underscore**.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9.-]{5,50}$",
        description="Certificate name (5-50 chars, letters/digits/'.'/'-'; NO underscore).",
    )
    type: Literal["CA", "TLS/SSL"] = Field(
        ...,
        description="'TLS/SSL' for a server certificate, 'CA' for a certificate authority.",
    )
    certificate: str | None = Field(None, description="PEM-encoded certificate.")
    privateKey: str | None = Field(
        None, description="PEM-encoded private key. Never logged and never returned."
    )
    certificateChain: str | None = Field(
        None, description="PEM-encoded intermediate chain, leaf-first."
    )
    passphrase: str | None = Field(None, description="Passphrase protecting the private key.")


class L7RuleDto(BaseModel):
    """One match condition of an L7 policy.

    All rules of a policy must match for it to fire.
    """

    model_config = ConfigDict(extra="forbid")

    compareType: Literal["CONTAINS", "ENDS_WITH", "EQUAL_TO", "REGEX", "STARTS_WITH"] = Field(
        ..., description="How ruleValue is compared against the attribute."
    )
    ruleType: Literal["PATH", "HOST_NAME"] = Field(
        ..., description="Which part of the request to compare: the path or the Host header."
    )
    ruleValue: str = Field(
        ..., min_length=1, max_length=255, description="The value to compare against."
    )


class _L7PolicyFieldsDto(BaseModel):
    """Action and rule fields shared by the L7 policy create and update bodies."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["REDIRECT_TO_POOL", "REDIRECT_TO_URL"] = Field(
        ..., description="What to do with a matching request."
    )
    redirectPoolId: str | None = Field(
        None, description="Target pool. Required when action is REDIRECT_TO_POOL."
    )
    redirectUrl: str | None = Field(
        None, max_length=100, description="Target URL. Required when action is REDIRECT_TO_URL."
    )
    redirectHttpCode: Literal[301, 302] | None = Field(
        None, description="Status code for a URL redirect: 301 permanent, 302 temporary."
    )
    keepQueryString: bool | None = Field(
        None, description="Carry the original query string over to the redirect."
    )
    rules: list[L7RuleDto] | None = Field(
        None, description="Match conditions; ALL must match for the policy to fire."
    )

    @model_validator(mode="after")
    def _action_matches_target(self):
        """Require the target the chosen action actually uses.

        The API rejects the mismatch with an unlabelled message, and a policy
        that redirects nowhere silently drops traffic — so it is caught here.
        """
        if self.action == "REDIRECT_TO_POOL" and not self.redirectPoolId:
            raise ValueError("action REDIRECT_TO_POOL requires redirectPoolId")
        if self.action == "REDIRECT_TO_URL" and not self.redirectUrl:
            raise ValueError("action REDIRECT_TO_URL requires redirectUrl")
        return self


class CreateL7PolicyDto(_L7PolicyFieldsDto):
    """Request body for ``POST .../listeners/{listenerId}/l7policies``."""

    name: str = Field(..., pattern=NAME_PATTERN, description="Policy name (5-50 chars).")


class UpdateL7PolicyDto(_L7PolicyFieldsDto):
    """Request body for ``PUT .../l7policies/{policyId}``.

    The name cannot be changed. ``rules`` is a full replacement: omitting it may
    clear the policy's match conditions, which would make it match everything —
    read the policy with ``get_l7_policy`` and resend them.
    """


class ReorderPolicyDto(BaseModel):
    """One entry of a policy reordering."""

    model_config = ConfigDict(extra="forbid")

    policyId: str = Field(..., description="Policy to move.")
    position: int = Field(
        ...,
        ge=0,
        description=(
            "New position. The API's two descriptions contradict each other — the field "
            "says zero-based, the surrounding list says 'start from 1'. Read the current "
            "positions from list_l7_policies and stay in the same numbering."
        ),
    )


class ReorderPoliciesDto(BaseModel):
    """Request body for ``PUT .../listeners/{listenerId}/reorderL7Policies``."""

    model_config = ConfigDict(extra="forbid")

    policies: list[ReorderPolicyDto] = Field(..., description="The policies and their new order.")
