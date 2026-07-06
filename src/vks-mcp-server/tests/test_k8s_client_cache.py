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
