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
    with (
        patch("greennode.vks_mcp_server.k8s_client_cache.K8sApis") as MockK8sApis,
        patch("greennode.vks_mcp_server.k8s_client_cache._probe_endpoint") as probe,
    ):
        mock_instance = MagicMock()
        MockK8sApis.from_api_client.return_value = mock_instance
        client = await cache.get_client("k8s-123")
        assert client == mock_instance
        MockK8sApis.from_api_client.assert_called_once()
        # the probe guards every client build, with the kubeconfig's server URL
        probe.assert_called_once_with("https://10.0.0.1:6443")


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
    with (
        patch("greennode.vks_mcp_server.k8s_client_cache.K8sApis") as MockK8sApis,
        patch("greennode.vks_mcp_server.k8s_client_cache._probe_endpoint"),
    ):
        mock_instance = MagicMock()
        MockK8sApis.from_api_client.return_value = mock_instance
        await cache.get_client("k8s-123")
        await cache.get_client("k8s-123")
        assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_unreachable_endpoint_fails_fast_and_does_not_poison_cache(sample_config):
    """A PRIVATE cluster's endpoint must fail in seconds with an actionable
    error — and the failed client must NOT be cached, so other clusters and
    later retries are unaffected (field bug: after a private-cluster call,
    the next public-cluster call appeared to 'reuse' the private kubeconfig
    because the server was still stuck connecting to it)."""
    _mock_iam()
    respx.get(f"{VKS_BASE}/v1/clusters/k8s-priv/kubeconfig").mock(
        return_value=httpx.Response(200, text=SAMPLE_KUBECONFIG),
    )
    config = load_config(sample_config)
    tm = TokenManager(config)
    vks_client = VksClient(config, tm)
    cache = K8sClientCache(vks_client)
    with patch(
        "greennode.vks_mcp_server.k8s_client_cache._probe_endpoint",
        side_effect=ValueError("endpoint is not reachable (PRIVATE cluster?)"),
    ):
        with pytest.raises(ValueError, match="not reachable"):
            await cache.get_client("k8s-priv")
    assert len(cache._cache) == 0  # nothing poisoned


def test_probe_endpoint_error_teaches_private_cluster():
    """The unreachable-endpoint error names the likely cause (private cluster)
    and says other clusters are unaffected."""
    from greennode.vks_mcp_server.k8s_client_cache import _probe_endpoint

    with pytest.raises(ValueError) as exc_info:
        _probe_endpoint("https://10.255.255.1:6443", timeout=0.05)
    msg = str(exc_info.value)
    assert "not reachable" in msg
    assert "PRIVATE" in msg and "enablePrivateCluster" in msg
    assert "other" in msg.lower()  # other clusters are unaffected


def test_server_url_of_follows_current_context():
    from greennode.vks_mcp_server.k8s_client_cache import _server_url_of

    cfg = {
        "current-context": "b",
        "contexts": [
            {"name": "a", "context": {"cluster": "cl-a"}},
            {"name": "b", "context": {"cluster": "cl-b"}},
        ],
        "clusters": [
            {"name": "cl-a", "cluster": {"server": "https://a:6443"}},
            {"name": "cl-b", "cluster": {"server": "https://b:6443"}},
        ],
    }
    assert _server_url_of(cfg) == "https://b:6443"


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
