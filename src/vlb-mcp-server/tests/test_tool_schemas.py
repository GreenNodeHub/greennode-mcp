"""Tool input schemas as an MCP client sees them.

Every other test file constructs a handler and calls the method directly, which
bypasses the tool schema entirely — `Literal`, `Field(ge=, le=)` and the DTO
validators never run. That is how `MemberDto.weight` carried `ge=1` while the
gateway documents 0-256: no test could see it.

These tests read the registered schema instead, and the expected values are the
gateway's, transcribed from its DTO annotations and confirmed against its
dry-run validator (`POST /v2/{projectId}/validation`).
"""

from __future__ import annotations

import pytest
from .helpers import CA, LB, LISTENER, POOL, SUBNET
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.server import register_handlers
from mcp.server.mcpserver import MCPServer


@pytest.fixture
async def tools(config, client):
    """Every tool the server registers in write mode, by name."""
    mcp = MCPServer("t")
    register_handlers(mcp, config, client, None, DiscoveryCache(ttl_config={}), allow_write=True)
    return {tool.name: tool for tool in await mcp.list_tools()}


def _schema(tools, name):
    return tools[name].input_schema


def _defs(schema):
    return schema.get("$defs") or schema.get("definitions") or {}


def _prop(schema, dotted):
    """Walk a dotted path through properties.

    Resolves the three wrappers pydantic emits along the way: ``$ref`` into
    ``$defs``, ``anyOf`` for an optional field, and ``items`` for a list — so
    ``body.members.weight`` reaches the member model's own field.
    """
    defs = _defs(schema)

    def deref(node):
        for _ in range(4):  # a ref may sit behind an anyOf behind an items
            if "$ref" in node:
                node = defs[node["$ref"].rsplit("/", 1)[-1]]
                continue
            for key in ("anyOf", "allOf"):
                if key in node:
                    node = next(
                        (b for b in node[key] if b.get("type") != "null" or "$ref" in b),
                        node,
                    )
                    break
            else:
                if "items" in node and "properties" not in node:
                    node = node["items"]
                    continue
                return node
        return node

    node = schema
    for part in dotted.split("."):
        node = deref(node)["properties"][part]
    return deref(node)


def _enum(schema, dotted):
    node = _prop(schema, dotted)
    if "enum" in node:
        return set(node["enum"])
    # A Literal inside an optional lands as anyOf[{enum}, {null}], and a
    # list[Literal] as {"items": {"enum": ...}}.
    if "items" in node:
        return set(node["items"].get("enum", []))
    for branch in node.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
        if "items" in branch:
            return set(branch["items"].get("enum", []))
    raise AssertionError(f"{dotted} exposes no enum: {node}")


def _bounds(schema, dotted):
    node = _prop(schema, dotted)
    return node.get("minimum"), node.get("maximum")


# --- enums match the gateway's allowed values ---------------------------------


@pytest.mark.parametrize(
    "tool,path,expected",
    [
        ("create_load_balancer", "body.type", {"Layer 4", "Layer 7"}),
        ("create_load_balancer", "body.scheme", {"Internet", "Internal"}),
        ("create_pool", "body.algorithm", {"ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"}),
        ("create_pool", "body.poolProtocol", {"HTTP", "TCP", "UDP", "PROXY"}),
        (
            "create_pool",
            "body.healthMonitor.healthCheckProtocol",
            {"TCP", "HTTP", "HTTPS", "PING-UDP"},
        ),
        ("create_pool", "body.healthMonitor.healthCheckMethod", {"GET", "POST", "PUT"}),
        ("create_pool", "body.healthMonitor.httpVersion", {"1.0", "1.1"}),
        ("create_listener", "body.listenerProtocol", {"HTTP", "HTTPS", "TCP", "UDP"}),
        ("create_listener", "body.defaultAction", {"accept", "drop"}),
        ("create_listener", "body.tlsSecurityPolicy", {"COMPATIBLE", "MODERN", "STRICT"}),
        ("create_listener", "body.alpnProtocols", {"http/1.1", "http/1.0", "h2"}),
        ("import_certificate", "body.type", {"CA", "TLS/SSL"}),
        ("create_l7_policy", "body.action", {"REDIRECT_TO_POOL", "REDIRECT_TO_URL"}),
        ("create_l7_policy", "body.redirectHttpCode", {301, 302}),
        (
            "create_l7_policy",
            "body.rules.compareType",
            {"CONTAINS", "ENDS_WITH", "EQUAL_TO", "REGEX", "STARTS_WITH"},
        ),
        ("create_l7_policy", "body.rules.ruleType", {"PATH", "HOST_NAME"}),
    ],
)
def test_enum_matches_the_gateway(tools, tool, path, expected):
    assert _enum(_schema(tools, tool), path) == expected


def test_layer_values_keep_their_space(tools):
    """'Layer 4', not 'LAYER4' — the API compares the string with the space."""
    assert "Layer 4" in _enum(_schema(tools, "create_load_balancer"), "body.type")


# --- numeric bounds match the gateway's @Range annotations ---------------------


@pytest.mark.parametrize(
    "tool,path,expected",
    [
        # MemberRequest: @Range(min = 0, max = 256) on weight — 0 drains a
        # member and is valid; the DTO used to declare ge=1 and reject it.
        ("create_pool", "body.members.weight", (0, 256)),
        ("create_pool", "body.members.port", (1, 65535)),
        ("create_pool", "body.members.monitorPort", (1, 65535)),
        ("create_pool", "body.healthMonitor.healthyThreshold", (2, 10)),
        ("create_pool", "body.healthMonitor.unhealthyThreshold", (2, 10)),
        ("create_pool", "body.healthMonitor.timeout", (2, 120)),
        ("create_pool", "body.healthMonitor.interval", (5, 3600)),
        ("create_listener", "body.listenerProtocolPort", (1, 65535)),
        ("create_listener", "body.timeoutClient", (1, 3600)),
        ("create_listener", "body.timeoutMember", (1, 3600)),
        ("create_listener", "body.timeoutConnection", (1, 3600)),
    ],
)
def test_numeric_bounds_match_the_gateway(tools, tool, path, expected):
    assert _bounds(_schema(tools, tool), path) == expected


def test_autoscale_max_size_is_capped_at_fifteen(tools):
    """ScaleValidationHelper.MAX_ALLOWED_SIZE = 15."""
    schema = _schema(tools, "configure_load_balancer_scaling")
    assert schema["properties"]["max_size"]["maximum"] == 15


# --- name patterns ------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,path",
    [
        ("create_load_balancer", "body.name"),
        ("create_pool", "body.poolName"),
        ("create_listener", "body.listenerName"),
        ("create_l7_policy", "body.name"),
        ("create_pool", "body.members.name"),
    ],
)
def test_resource_names_share_one_pattern(tools, tool, path):
    assert _prop(_schema(tools, tool), path)["pattern"] == r"^[a-zA-Z0-9_\-.]{5,50}$"


def test_certificate_name_is_the_one_that_forbids_underscores(tools):
    pattern = _prop(_schema(tools, "import_certificate"), "body.name")["pattern"]
    assert pattern == r"^[a-zA-Z0-9.-]{5,50}$"
    assert "_" not in pattern


# --- required-ness the gateway enforces ---------------------------------------


def test_health_monitor_probe_timings_are_required(tools):
    """All four carry @NotNull; the gateway answers 400 "Timeout must be specified."."""
    monitor = _prop(_schema(tools, "create_pool"), "body.healthMonitor")
    assert set(monitor["required"]) >= {
        "healthCheckProtocol",
        "healthyThreshold",
        "unhealthyThreshold",
        "timeout",
        "interval",
    }


def test_a_pool_cannot_be_created_without_a_health_monitor(tools):
    """`pool.healthMonitor: must not be null` — for the embedded pool too."""
    assert "healthMonitor" in _prop(_schema(tools, "create_pool"), "body")["required"]
    embedded = _prop(_schema(tools, "create_load_balancer"), "body.pool")
    assert "healthMonitor" in embedded["required"]


def test_member_backup_is_always_serialised(tools):
    """@NotNull on a Boolean: false must survive exclude_none, so it has a default."""
    member = _prop(_schema(tools, "create_pool"), "body.members")
    assert member["properties"]["backup"]["default"] is False


# --- the paging params that only work with a name -----------------------------


def test_list_load_balancers_documents_which_filters_are_real(tools):
    """status / subnet_id / vpc_id are applied locally, so the schema says so."""
    props = _schema(tools, "list_load_balancers")["properties"]
    for local in ("status", "subnet_id", "vpc_id"):
        assert "this server" in props[local]["description"]
    for server_side in ("name", "zone_id"):
        assert "server-side" in props[server_side]["description"]
    for paged in ("page", "size"):
        assert "`name`" in props[paged]["description"]


# --- every write tool describes its result ------------------------------------


def test_write_tools_declare_an_output_schema(tools):
    missing = [
        name
        for name, tool in tools.items()
        if not (tool.annotations and tool.annotations.read_only_hint)
        and tool.output_schema is None
    ]
    assert missing == []


# --- the schema actually rejects, end to end ----------------------------------


@pytest.mark.parametrize(
    "field,value",
    [("weight", 257), ("port", 0), ("port", 70000), ("monitorPort", 0)],
)
def test_member_bounds_reject_out_of_range_values(field, value):
    from greennode.vlb_mcp_server.models import MemberDto
    from pydantic import ValidationError

    kwargs = {"ipAddress": "10.0.0.1", "port": 80, "backup": False, field: value}
    with pytest.raises(ValidationError):
        MemberDto(**kwargs)


def test_member_weight_zero_is_accepted():
    """The gateway allows it (`@Range(min = 0, ...)`) and it means "drain me"."""
    from greennode.vlb_mcp_server.models import MemberDto

    assert MemberDto(ipAddress="10.0.0.1", port=80, backup=False, weight=0).weight == 0


def test_ids_used_in_paths_are_declared_as_strings(tools):
    """A non-string id would bypass validate_lb_id's type assumptions."""
    for tool, field, sample in [
        ("get_load_balancer", "load_balancer_id", LB),
        ("get_pool", "pool_id", POOL),
        ("get_listener", "listener_id", LISTENER),
        ("get_certificate", "certificate_id", CA),
        ("list_subnets", "vpc_id", SUBNET),
    ]:
        assert _schema(tools, tool)["properties"][field]["type"] == "string", tool
        assert isinstance(sample, str)
