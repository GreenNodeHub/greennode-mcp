"""Certificates (`/cas`): flat pagination, the bare-object get, and secret hygiene."""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from .helpers import CA, PROJECT, VLB_HCM3, data_env, mock_iam, paging_env
from greennode.vlb_mcp_server.certificate_handler import CertificateHandler
from greennode.vlb_mcp_server.models import ImportCertificateDto
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


CAS = f"{VLB_HCM3}/v2/{PROJECT}/cas"

SAMPLE_CA = {
    "uuid": CA,
    "name": "wildcard.example.com",
    "certificateType": "TLS/SSL",
    "expiredAt": "2027-01-01",
    "importedAt": "2026-01-01T00:00:00Z",
    "notBefore": 1767225600,
    "notAfter": 1798761600,
    "domainName": "*.example.com",
    "issuer": "Example CA",
    "keyAlgorithm": "RSA-2048",
    "signatureAlgorithm": "SHA256withRSA",
    "serial": "0A1B2C",
    "subject": "CN=*.example.com",
    "subjectAlternativeNames": ["example.com", "*.example.com"],
    "inUse": True,
}


@pytest.fixture
def certs_rw(config, client, no_cache):
    return CertificateHandler(MCPServer("t"), config, client, no_cache, allow_write=True)


@pytest.fixture
def certs_ro(config, client, no_cache):
    return CertificateHandler(MCPServer("t"), config, client, no_cache, allow_write=False)


# --- registration ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_registers_no_write_tools(certs_ro):
    assert {t.name for t in await certs_ro.mcp.list_tools()} == {
        "list_certificates",
        "get_certificate",
    }


@pytest.mark.asyncio
async def test_write_mode_adds_exactly_the_mutating_tools(certs_rw, certs_ro):
    read = {t.name for t in await certs_ro.mcp.list_tools()}
    write = {t.name for t in await certs_rw.mcp.list_tools()}
    assert write - read == {"import_certificate", "delete_certificate"}


# --- reads ------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_list_certificates_uses_flat_one_based_pagination(certs_ro):
    """Unlike the LB list, /cas documents flat name/page/size with page defaulting to 1."""
    mock_iam(respx.mock)
    route = respx.get(CAS).mock(return_value=httpx.Response(200, json=paging_env([SAMPLE_CA])))
    result = await certs_ro.list_certificates(
        name="wildcard", page=1, size=10, region="HCM-3", refresh=True
    )
    assert dict(route.calls[0].request.url.params) == {
        "name": "wildcard",
        "page": "1",
        "size": "10",
    }
    cert = result.certificates[0]
    assert (cert.id, cert.type, cert.in_use) == (CA, "TLS/SSL", True)
    assert cert.subject_alternative_names == ["example.com", "*.example.com"]


@respx.mock
@pytest.mark.asyncio
async def test_list_certificates_omits_an_unset_name(certs_ro):
    mock_iam(respx.mock)
    route = respx.get(CAS).mock(return_value=httpx.Response(200, json=paging_env([])))
    await certs_ro.list_certificates(name=None, page=1, size=10, region="HCM-3", refresh=True)
    assert "name" not in dict(route.calls[0].request.url.params)


@respx.mock
@pytest.mark.asyncio
async def test_get_certificate_reads_a_bare_object(certs_ro):
    """getCertificateAuthorityById returns CADto directly, not wrapped in `data`."""
    mock_iam(respx.mock)
    respx.get(f"{CAS}/{CA}").mock(return_value=httpx.Response(200, json=SAMPLE_CA))
    cert = await certs_ro.get_certificate(certificate_id=CA, region="HCM-3")
    assert cert.id == CA
    assert cert.domain_name == "*.example.com"


@respx.mock
@pytest.mark.asyncio
async def test_get_certificate_also_reads_a_wrapped_object(certs_ro):
    mock_iam(respx.mock)
    respx.get(f"{CAS}/{CA}").mock(return_value=httpx.Response(200, json=data_env(SAMPLE_CA)))
    cert = await certs_ro.get_certificate(certificate_id=CA, region="HCM-3")
    assert cert.id == CA


# --- writes ------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_import_certificate_sends_the_body_and_parses_a_201(certs_rw):
    mock_iam(respx.mock)
    route = respx.post(CAS).mock(return_value=httpx.Response(201, json=data_env(SAMPLE_CA)))
    result = await certs_rw.import_certificate(
        body=ImportCertificateDto(
            name="wildcard.example.com",
            type="TLS/SSL",
            certificate="-----BEGIN CERTIFICATE-----",
            privateKey="-----BEGIN PRIVATE KEY-----",
        ),
        region="HCM-3",
    )
    assert json.loads(route.calls[0].request.content) == {
        "name": "wildcard.example.com",
        "type": "TLS/SSL",
        "certificate": "-----BEGIN CERTIFICATE-----",
        "privateKey": "-----BEGIN PRIVATE KEY-----",
    }
    assert result.id == CA


@respx.mock
@pytest.mark.asyncio
async def test_import_certificate_never_returns_the_private_key(certs_rw):
    """The private key must not travel back out through a tool result."""
    mock_iam(respx.mock)
    respx.post(CAS).mock(
        return_value=httpx.Response(
            201, json=data_env({**SAMPLE_CA, "privateKey": "SUPER-SECRET"})
        )
    )
    result = await certs_rw.import_certificate(
        body=ImportCertificateDto(
            name="wildcard.example.com", type="TLS/SSL", privateKey="SUPER-SECRET"
        ),
        region="HCM-3",
    )
    assert "SUPER-SECRET" not in result.model_dump_json()


@respx.mock
@pytest.mark.asyncio
async def test_delete_certificate_tolerates_a_204(certs_rw):
    mock_iam(respx.mock)
    route = respx.delete(f"{CAS}/{CA}").mock(return_value=httpx.Response(204))
    message = await certs_rw.delete_certificate(certificate_id=CA, region="HCM-3")
    assert route.called and CA in message


# --- DTO strictness -------------------------------------------------------------------


def test_import_dto_accepts_the_slash_bearing_type():
    assert ImportCertificateDto(name="wildcard.example.com", type="CA").type == "CA"
    assert ImportCertificateDto(name="wildcard.example.com", type="TLS/SSL").type == "TLS/SSL"


def test_import_dto_rejects_an_unknown_type():
    with pytest.raises(ValidationError):
        ImportCertificateDto(name="wildcard.example.com", type="PEM")


def test_import_dto_rejects_an_underscore_in_the_name():
    """The CA name pattern is the one place underscores are NOT allowed."""
    with pytest.raises(ValidationError):
        ImportCertificateDto(name="wildcard_example", type="CA")


def test_import_dto_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ImportCertificateDto(name="wildcard.example.com", type="CA", bogus=1)


# --- guards ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_tools_refuse_in_read_only_mode(certs_ro):
    with pytest.raises(ValueError, match="--allow-write"):
        await certs_ro.delete_certificate(certificate_id=CA, region="HCM-3")


@pytest.mark.asyncio
async def test_get_certificate_validates_the_id(certs_ro):
    with pytest.raises(ValueError, match="Invalid certificate_id"):
        await certs_ro.get_certificate(certificate_id="../secrets", region="HCM-3")
