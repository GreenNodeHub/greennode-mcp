"""Shared constants, IAM/project mocks and envelope builders for the vLB tests.

Sample payloads deliberately use the **response** spellings the vLB gateway
actually sends (``loadBalancerSchema``, ``loadBalanceMethod``, ``address``,
``protocolPort``) rather than the request spellings, so a model that only works
against tidied-up fixtures fails here instead of in production.
"""

from __future__ import annotations

import httpx
import respx


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"

VLB_HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vlb-gateway"
VLB_HAN = "https://han-1.api.vngcloud.vn/vserver/vlb-gateway"
VSERVER_HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
VSERVER_HAN = "https://han-1.api.vngcloud.vn/vserver/vserver-gateway"

PROJECT = "pro-test-0001"
LB = "lb-1111-2222"
LISTENER = "lis-1111-2222"
POOL = "pool-1111-2222"
POLICY = "pol-1111-2222"
CA = "ca-1111-2222"
SUBNET = "sub-1111-2222"


def mock_iam(mock: respx.MockRouter) -> None:
    """Answer the IAM token endpoint so no test ever needs credentials."""
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


def mock_project(mock: respx.MockRouter, base: str = VSERVER_HCM3, project: str = PROJECT):
    """Answer ``GET /v1/projects`` on the **vServer** gateway (vLB has no such endpoint)."""
    return mock.get(f"{base}/v1/projects").mock(
        return_value=httpx.Response(200, json={"data": [{"projectId": project}]})
    )


def data_env(obj):
    """Wrap *obj* the way vLB wraps a single object: ``{"data": {...}}``."""
    return {"data": obj}


def list_env(items):
    """Wrap *items* the way vLB wraps a sub-resource list: ``{"data": [...]}``."""
    return {"data": list(items)}


def paging_env(items, total=None, page=1, page_size=500):
    """Build the ``PagingXxx`` envelope used by the top-level collections."""
    items = list(items)
    return {
        "listData": items,
        "page": page,
        "pageSize": page_size,
        "totalPage": 1,
        "totalItem": total if total is not None else len(items),
    }


SAMPLE_LB = {
    "uuid": LB,
    "name": "prod-lb",
    "displayStatus": "ACTIVE",
    "progressStatus": "COMPLETED",
    "loadBalancerSchema": "Internet",
    "type": "Layer 7",
    "displayType": "Application Load Balancer",
    "address": "203.0.113.10",
    "subnetId": SUBNET,
    "packageId": "lbp-small",
    "zoneId": "HCM03-1A",
}

SAMPLE_LISTENER = {
    "uuid": LISTENER,
    "name": "https-443",
    "protocol": "HTTPS",
    "protocolPort": 443,
    "displayStatus": "ACTIVE",
    "progressStatus": "COMPLETED",
    "defaultPoolId": POOL,
    "allowedCidrs": "0.0.0.0/0",
    "blockedCidrs": "",
    "clientCertificateAuthentication": None,
    "headers": ["X-Forwarded-For"],
    "insertHeaders": [{"headerName": "X-Forwarded-Proto", "headerValue": "https"}],
}

SAMPLE_POOL = {
    "uuid": POOL,
    "name": "web-pool",
    "protocol": "HTTP",
    "loadBalanceMethod": "ROUND_ROBIN",
    "displayStatus": "ACTIVE",
    "progressStatus": "COMPLETED",
    "stickiness": False,
    "tlsEncryption": False,
}

SAMPLE_MEMBERS = [
    {
        "uuid": "mem-1",
        "address": "10.0.0.11",
        "protocolPort": 80,
        "weight": 1,
        "name": "web-01",
        "monitorPort": 80,
        "backup": False,
        "displayStatus": "ACTIVE",
    },
    {
        "uuid": "mem-2",
        "address": "10.0.0.12",
        "protocolPort": 80,
        "weight": 1,
        "name": "web-02",
        "monitorPort": 80,
        "backup": False,
        "displayStatus": "ACTIVE",
    },
]
