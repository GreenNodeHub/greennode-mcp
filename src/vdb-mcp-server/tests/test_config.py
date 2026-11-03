"""Tests for vDB region/service endpoint resolution."""

from __future__ import annotations

import pytest
from greennode.vdb_mcp_server.config import (
    REGIONS,
    VDB_KAFKA_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_POSTGRESQL_SERVICE,
    VDB_RELATIONAL_SERVICE,
    load_config,
)


ALL_SERVICES = (
    VDB_RELATIONAL_SERVICE,
    VDB_MEMORY_SERVICE,
    VDB_POSTGRESQL_SERVICE,
    VDB_KAFKA_SERVICE,
)


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


def test_every_family_has_an_endpoint_in_every_region():
    for region in REGIONS:
        for service in ALL_SERVICES:
            assert REGIONS[region][service].startswith("https://vdb-gateway.vngcloud.vn/")


@pytest.mark.parametrize("service", ALL_SERVICES)
def test_gateway_is_not_region_scoped(config, service):
    """One gateway answers for the whole account; the project comes from the token.

    Both region keys therefore resolve to the same URL. If this ever stops
    being true, the tools have to grow a `region` parameter -- today they
    deliberately have none.
    """
    assert config.get_base_url("HCM-3", service) == config.get_base_url("HAN", service)


def test_none_region_falls_back_to_the_profile_default(config):
    assert config.get_base_url(None, VDB_RELATIONAL_SERVICE) == config.get_base_url(
        "HCM-3", VDB_RELATIONAL_SERVICE
    )


def test_unknown_region_is_rejected(config):
    with pytest.raises(ValueError, match="does not exist"):
        config.get_base_url("MOON-1", VDB_RELATIONAL_SERVICE)


def test_family_prefix_is_part_of_the_base_url(config):
    """Paths in the handlers are written without the family prefix.

    `GET /vdb-relational/v1/database-instances` is called as `/v1/database-instances`
    against the relational service, so the prefix must live in the base URL.
    """
    assert config.get_base_url(None, VDB_RELATIONAL_SERVICE).endswith("/vdb-relational")
    assert config.get_base_url(None, VDB_MEMORY_SERVICE).endswith("/vdb-memory")
    assert config.get_base_url(None, VDB_POSTGRESQL_SERVICE).endswith("/vdb-postgresql")
    assert config.get_base_url(None, VDB_KAFKA_SERVICE).endswith("/vdb-kafka")


def test_load_config_reads_the_profile(config):
    assert config.client_id == "test-client-id"
    assert config.default_region == "HCM-3"
    assert config.project_id == "pro-test-0001"
