"""Cross-field rules the gateway enforces, checked before the request goes out.

Every case below was run through the gateway's dry-run validator
(`POST /v2/{projectId}/validation`, which executes the real
CreateLoadBalancerValidator without creating anything) and the expected verdict
is what it returned. The messages are close to the gateway's own so an agent
that has seen one recognises the other.

These rules live on the DTOs rather than in `validate_load_balancer_create`, so
they also guard `create_pool` and `create_listener` — a validate-only check is
one an agent is free to skip.
"""

from __future__ import annotations

import pytest
from greennode.vlb_mcp_server.models import (
    CreateHealthMonitorDto,
    CreateListenerDto,
    CreateLoadBalancerDto,
    CreatePoolDto,
    EmbeddedListenerDto,
    EmbeddedPoolDto,
    MemberDto,
    ScalingConfigDto,
)
from pydantic import ValidationError


TCP_PROBE = {
    "healthCheckProtocol": "TCP",
    "healthyThreshold": 3,
    "unhealthyThreshold": 3,
    "timeout": 5,
    "interval": 30,
}
HTTP_PROBE = {
    **TCP_PROBE,
    "healthCheckProtocol": "HTTP",
    "healthCheckPath": "/health",
    "healthCheckMethod": "GET",
    "successCode": "200",
    "httpVersion": "1.0",
}
LISTENER_TIMEOUTS = {"timeoutClient": 50, "timeoutMember": 50, "timeoutConnection": 5}


def _pool(**over):
    body = {
        "poolName": "web-pool",
        "algorithm": "ROUND_ROBIN",
        "poolProtocol": "TCP",
        "healthMonitor": dict(TCP_PROBE),
    }
    body.update(over)
    return body


def _listener(**over):
    body = {"listenerName": "web-listen", "listenerProtocol": "TCP", "listenerProtocolPort": 80}
    body.update(LISTENER_TIMEOUTS)
    body.update(over)
    return body


# --- baselines the gateway accepts --------------------------------------------


def test_a_tcp_pool_baseline_is_accepted():
    assert CreatePoolDto(**_pool()).poolProtocol == "TCP"


def test_an_http_pool_baseline_is_accepted():
    pool = CreatePoolDto(
        **_pool(
            poolProtocol="HTTP",
            stickiness=False,
            tlsEncryption=False,
            healthMonitor=dict(HTTP_PROBE),
        )
    )
    assert pool.stickiness is False


def test_a_tcp_listener_baseline_is_accepted():
    assert CreateListenerDto(**_listener()).listenerProtocolPort == 80


# --- health monitor (CreatePoolValidator) -------------------------------------


def test_probe_timeout_must_be_below_interval():
    """Equal is not enough: the check is `timeout >= interval` -> 400."""
    with pytest.raises(ValidationError, match="Timeout must be less than interval"):
        CreateHealthMonitorDto(**{**TCP_PROBE, "timeout": 30, "interval": 30})


@pytest.mark.parametrize(
    "field", ["healthyThreshold", "unhealthyThreshold", "timeout", "interval"]
)
def test_probe_timings_cannot_be_omitted(field):
    """All four carry @NotNull — "Timeout must be specified." and friends."""
    body = {k: v for k, v in TCP_PROBE.items() if k != field}
    with pytest.raises(ValidationError, match="Field required"):
        CreateHealthMonitorDto(**body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("healthCheckPath", "/health"),
        ("healthCheckMethod", "GET"),
        ("successCode", "200"),
        ("httpVersion", "1.0"),
        ("domainName", "example.com"),
    ],
)
def test_a_tcp_probe_rejects_every_http_only_field(field, value):
    with pytest.raises(ValidationError, match="cannot be specified"):
        CreateHealthMonitorDto(**{**TCP_PROBE, field: value})


@pytest.mark.parametrize(
    "field", ["healthCheckPath", "healthCheckMethod", "successCode", "httpVersion"]
)
def test_an_http_probe_requires_every_http_field(field):
    body = {k: v for k, v in HTTP_PROBE.items() if k != field}
    with pytest.raises(ValidationError, match="must be specified"):
        CreateHealthMonitorDto(**body)


def test_http_version_1_1_needs_a_domain_name():
    with pytest.raises(ValidationError, match="Domain name must be specified"):
        CreateHealthMonitorDto(**{**HTTP_PROBE, "httpVersion": "1.1"})


def test_http_version_1_0_must_not_carry_a_domain_name():
    with pytest.raises(ValidationError, match="Domain name cannot be specified"):
        CreateHealthMonitorDto(**{**HTTP_PROBE, "domainName": "example.com"})


def test_http_version_1_1_with_a_domain_name_is_accepted():
    probe = CreateHealthMonitorDto(
        **{**HTTP_PROBE, "httpVersion": "1.1", "domainName": "example.com"}
    )
    assert probe.domainName == "example.com"


# --- pool stickiness / protocol pairing ---------------------------------------


@pytest.mark.parametrize(
    "missing,message",
    [
        ("stickiness", "Stickiness must be specified for HTTP pools"),
        ("tlsEncryption", "Tls Encryption must be specified for HTTP pools"),
    ],
)
def test_an_http_pool_requires_both_flags(missing, message):
    body = _pool(
        poolProtocol="HTTP", stickiness=False, tlsEncryption=False, healthMonitor=dict(HTTP_PROBE)
    )
    body.pop(missing)
    with pytest.raises(ValidationError, match=message):
        CreatePoolDto(**body)


@pytest.mark.parametrize(
    "field,message",
    [
        ("stickiness", "Stickiness cannot be specified for non-HTTP pools"),
        ("tlsEncryption", "Tls Encryption cannot be specified for non-HTTP pools"),
    ],
)
def test_a_non_http_pool_rejects_both_flags(field, message):
    with pytest.raises(ValidationError, match=message):
        CreatePoolDto(**_pool(**{field: True}))


@pytest.mark.parametrize("protocol", ["TCP", "HTTP", "HTTPS"])
def test_a_udp_pool_demands_a_ping_udp_probe(protocol):
    probe = dict(HTTP_PROBE) if protocol in ("HTTP", "HTTPS") else dict(TCP_PROBE)
    probe["healthCheckProtocol"] = protocol
    with pytest.raises(ValidationError, match="Valid values: PING-UDP"):
        CreatePoolDto(**_pool(poolProtocol="UDP", healthMonitor=probe))


@pytest.mark.parametrize("protocol", ["TCP", "PROXY"])
def test_only_a_udp_pool_may_use_a_ping_udp_probe(protocol):
    probe = {**TCP_PROBE, "healthCheckProtocol": "PING-UDP"}
    with pytest.raises(ValidationError, match="Valid values: TCP, HTTP, HTTPS"):
        CreatePoolDto(**_pool(poolProtocol=protocol, healthMonitor=probe))


def test_a_udp_pool_with_a_ping_udp_probe_is_accepted():
    probe = {**TCP_PROBE, "healthCheckProtocol": "PING-UDP"}
    assert CreatePoolDto(**_pool(poolProtocol="UDP", healthMonitor=probe)).poolProtocol == "UDP"


def test_a_pool_cannot_be_built_without_a_probe():
    body = _pool()
    body.pop("healthMonitor")
    with pytest.raises(ValidationError, match="Field required"):
        CreatePoolDto(**body)


# --- listener TLS / header pairing (CreateListenerValidator) ------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientCertificate", "secret-1"),
        ("certificateAuthorities", ["secret-1"]),
        ("defaultCertificateAuthority", "secret-1"),
        ("tlsSecurityPolicy", "MODERN"),
    ],
)
def test_a_non_https_listener_rejects_tls_fields(field, value):
    with pytest.raises(ValidationError, match="tls security policy for tcp listener"):
        CreateListenerDto(**_listener(**{field: value}))


def test_a_non_https_listener_rejects_alpn():
    with pytest.raises(ValidationError, match="only applicable for HTTPS listeners"):
        CreateListenerDto(**_listener(alpnProtocols=["h2"]))


def test_an_https_listener_needs_a_default_certificate():
    with pytest.raises(ValidationError, match="defaultCertificateAuthority"):
        CreateListenerDto(**_listener(listenerProtocol="HTTPS", listenerProtocolPort=443))


@pytest.mark.parametrize("protocol", ["TCP", "UDP"])
def test_layer_4_listeners_reject_inserted_headers(protocol):
    headers = [{"headerName": "X-Forwarded-For", "headerValue": "true"}]
    with pytest.raises(ValidationError, match=f"Headers cannot be specified for {protocol}"):
        CreateListenerDto(**_listener(listenerProtocol=protocol, insertHeaders=headers))


def test_an_http_listener_rejects_ssl_headers():
    """The X-SSL-* names exist for HTTPS; on HTTP they would always be empty."""
    headers = [{"headerName": "X-SSL-Client-CN", "headerValue": "true"}]
    with pytest.raises(ValidationError, match="'SSL' is not allowed for HTTP protocol"):
        CreateListenerDto(**_listener(listenerProtocol="HTTP", insertHeaders=headers))


def test_a_header_value_must_be_true():
    headers = [{"headerName": "X-Forwarded-For", "headerValue": "false"}]
    with pytest.raises(ValidationError, match="Invalid value for header"):
        CreateListenerDto(**_listener(listenerProtocol="HTTP", insertHeaders=headers))


def test_an_access_header_may_carry_its_own_value():
    headers = [{"headerName": "Access-Control-Max-Age", "headerValue": "600"}]
    listener = CreateListenerDto(**_listener(listenerProtocol="HTTP", insertHeaders=headers))
    assert listener.insertHeaders[0].headerValue == "600"


def test_duplicate_header_names_are_refused():
    headers = [
        {"headerName": "X-Forwarded-For", "headerValue": "true"},
        {"headerName": "X-Forwarded-For", "headerValue": "true"},
    ]
    with pytest.raises(ValidationError, match="Duplicated header name"):
        CreateListenerDto(**_listener(listenerProtocol="HTTP", insertHeaders=headers))


def test_inserted_headers_are_capped_at_1024_characters():
    headers = [{"headerName": f"X-{'a' * 60}-{i}", "headerValue": "true"} for i in range(20)]
    with pytest.raises(ValidationError, match="at most 1024"):
        CreateListenerDto(**_listener(listenerProtocol="HTTP", insertHeaders=headers))


# --- autoscale bounds (ScaleValidationHelper) ---------------------------------


def test_max_size_is_capped_at_fifteen():
    with pytest.raises(ValidationError, match="less than or equal to 15"):
        ScalingConfigDto(maxSize=16)


def test_min_size_cannot_exceed_max_size():
    with pytest.raises(ValidationError, match="cannot be greater than max size"):
        ScalingConfigDto(maxSize=3, minSize=5)


def test_equal_min_and_max_is_accepted():
    assert ScalingConfigDto(maxSize=3, minSize=3).minSize == 3


# --- members ------------------------------------------------------------------


def test_monitor_port_defaults_to_the_service_port():
    """Omitting it sends 0 to a Java primitive int and fails @Range(1, 65535)."""
    member = MemberDto(ipAddress="10.0.0.1", port=8080, backup=False)
    assert member.monitorPort == 8080


def test_an_explicit_monitor_port_is_kept():
    member = MemberDto(ipAddress="10.0.0.1", port=8080, backup=False, monitorPort=9000)
    assert member.monitorPort == 9000


def test_weight_spans_zero_to_256():
    assert MemberDto(ipAddress="10.0.0.1", port=80, backup=False, weight=0).weight == 0
    assert MemberDto(ipAddress="10.0.0.1", port=80, backup=False, weight=256).weight == 256
    with pytest.raises(ValidationError, match="less than or equal to 256"):
        MemberDto(ipAddress="10.0.0.1", port=80, backup=False, weight=257)


# --- load-balancer-wide rules (validate_load_balancer_create) -----------------
# These need to know the load balancer's type, which a listener or pool DTO
# cannot see, so they stay in the handler's validator.


def _lb(**over):
    body = {
        "name": "prod-lb",
        "packageId": "lbp-small",
        "scheme": "Internet",
        "subnetId": "sub-1111",
        "type": "Layer 4",
    }
    body.update(over)
    return CreateLoadBalancerDto(**body)


@pytest.mark.asyncio
async def test_a_layer_4_load_balancer_cannot_terminate_http(lbs_ro):
    body = _lb(listener=EmbeddedListenerDto(**_listener(listenerProtocol="HTTP")))
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is False
    assert any("cannot terminate HTTP" in e for e in result.errors)


@pytest.mark.asyncio
async def test_a_layer_7_load_balancer_only_takes_an_http_pool(lbs_ro):
    body = _lb(
        type="Layer 7",
        listener=EmbeddedListenerDto(
            **_listener(listenerProtocol="HTTPS", defaultCertificateAuthority="secret-1")
        ),
        pool=EmbeddedPoolDto(**_pool(poolProtocol="PROXY")),
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is False
    assert any("cannot use a PROXY pool" in e for e in result.errors)


@pytest.mark.asyncio
async def test_a_tcp_listener_constrains_the_pool_protocol(lbs_ro):
    """ "Pool protocol must be TCP or PROXY if listener protocol is TCP"."""
    body = _lb(
        listener=EmbeddedListenerDto(**_listener(listenerProtocol="TCP")),
        pool=EmbeddedPoolDto(
            **_pool(
                poolProtocol="UDP",
                healthMonitor={**TCP_PROBE, "healthCheckProtocol": "PING-UDP"},
            )
        ),
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is False
    assert any("must be PROXY or TCP" in e or "TCP or PROXY" in e for e in result.errors)


@pytest.mark.asyncio
async def test_a_layer_7_load_balancer_cannot_probe_with_https(lbs_ro):
    """L7 allows only TCP and HTTP probes, even though the pool speaks HTTP."""
    body = _lb(
        type="Layer 7",
        pool=EmbeddedPoolDto(
            **_pool(
                poolProtocol="HTTP",
                stickiness=False,
                tlsEncryption=False,
                healthMonitor={**HTTP_PROBE, "healthCheckProtocol": "HTTPS"},
            )
        ),
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.ok is False
    assert any("cannot health-check with HTTPS" in e for e in result.errors)


@pytest.mark.asyncio
async def test_a_coherent_layer_4_body_validates_clean(lbs_ro):
    body = _lb(
        listener=EmbeddedListenerDto(**_listener(listenerProtocol="TCP")),
        pool=EmbeddedPoolDto(**_pool(poolProtocol="TCP")),
    )
    result = await lbs_ro.validate_load_balancer_create(body=body)
    assert result.errors == []
    assert result.ok is True
    # The name is permanent, so that is always worth saying.
    assert any("cannot be renamed" in w for w in result.warnings)
