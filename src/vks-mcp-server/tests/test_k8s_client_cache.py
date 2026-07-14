import httpx
import pytest
import respx
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import load_config
from greennode.vks_mcp_server.k8s_client_cache import K8sClientCache
from unittest.mock import MagicMock, patch


VKS_BASE = "https://vks.api.vngcloud.vn"
IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"

SAMPLE_KUBECONFIG = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: dGVzdC1jYS1kYXRh
    server: https://10.0.0.1:6443
  name: test-cluster
contexts:
- context:
    cluster: test-cluster
    user: admin
  name: admin@test-cluster
current-context: admin@test-cluster
kind: Config
users:
- name: admin
  user:
    client-certificate-data: dGVzdC1jZXJ0
    client-key-data: dGVzdC1rZXk=
"""


def _mock_iam():
    respx.post(IAM_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "accessToken": "t",
                "refreshToken": "r",
                "expiresIn": 1800,
                "refreshExpiresIn": 3600,
            },
        )
    )


@respx.mock
@pytest.mark.asyncio
async def test_get_client_fetches_kubeconfig(sample_config):
    _mock_iam()
    respx.get(f"{VKS_BASE}/v1/clusters/k8s-123/kubeconfig").mock(
        return_value=httpx.Response(200, text=SAMPLE_KUBECONFIG),
    )
    config = load_config(sample_config)
    tm = TokenManager(config)
    vks_client = VksClient(config, tm)
    cache = K8sClientCache(vks_client)
    with patch("greennode.vks_mcp_server.k8s_client_cache.K8sApis") as MockK8sApis:
        mock_instance = MagicMock()
        MockK8sApis.from_api_client.return_value = mock_instance
        client = await cache.get_client("k8s-123")
        assert client == mock_instance
        MockK8sApis.from_api_client.assert_called_once()


@respx.mock
@pytest.mark.asyncio
async def test_get_client_uses_cache(sample_config):
    _mock_iam()
    route = respx.get(f"{VKS_BASE}/v1/clusters/k8s-123/kubeconfig").mock(
        return_value=httpx.Response(200, text=SAMPLE_KUBECONFIG),
    )
    config = load_config(sample_config)
    tm = TokenManager(config)
    vks_client = VksClient(config, tm)
    cache = K8sClientCache(vks_client)
    with patch("greennode.vks_mcp_server.k8s_client_cache.K8sApis") as MockK8sApis:
        mock_instance = MagicMock()
        MockK8sApis.from_api_client.return_value = mock_instance
        await cache.get_client("k8s-123")
        await cache.get_client("k8s-123")
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_k8s_client_cache_isolated_per_identity(monkeypatch):
    """User A's kubernetes client (built from A's kubeconfig) must never be
    served to user B for the same cluster."""
    from greennode.mcp_core.http import user_token_var
    from greennode.vks_mcp_server.k8s_client_cache import K8sClientCache

    built = []

    async def fake_create(self, cluster_id, region):
        built.append(user_token_var.get())
        return object()

    monkeypatch.setattr(K8sClientCache, "_create_client", fake_create)
    cache = K8sClientCache(vks_client=None)

    t = user_token_var.set("token-user-a")
    try:
        a1 = await cache.get_client("k8s-1")
        a2 = await cache.get_client("k8s-1")
        assert a1 is a2  # same user: cached
    finally:
        user_token_var.reset(t)
    t = user_token_var.set("token-user-b")
    try:
        b1 = await cache.get_client("k8s-1")
        assert b1 is not a1  # different user: separate client
    finally:
        user_token_var.reset(t)
    assert built == ["token-user-a", "token-user-b"]
