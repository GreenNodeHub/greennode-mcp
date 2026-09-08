"""Certificates — the ``/cas`` tree, which is what an HTTPS listener serves.

Two things separate this from the rest of the package: the resource lives at
the project root rather than under a load balancer, and its list endpoint uses
plain ``name``/``page``/``size`` query parameters with a documented 1-based
``page`` — unlike the load balancer list, whose encoding the spec never pins
down.

Private keys go in and never come back out: ``ImportCertificateDto`` accepts
one, but no response model has a field for it.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.client import VlbClient
from greennode.vlb_mcp_server.config import Region, VlbConfig
from greennode.vlb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vlb_mcp_server.guards import require_write
from greennode.vlb_mcp_server.models import (
    CertificateItem,
    CertificateListData,
    ImportCertificateDto,
)
from greennode.vlb_mcp_server.paging import DEFAULT_PAGE_SIZE, as_list, total_items, unwrap_one
from greennode.vlb_mcp_server.project import require_project_id
from greennode.vlb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vlb_mcp_server.validators import validate_id
from pydantic import Field


class CertificateHandler:
    """Register and serve the certificate MCP tools."""

    def __init__(
        self,
        mcp,
        config: VlbConfig,
        client: VlbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_certificates", annotations=READ)(self.list_certificates)
        self.mcp.tool(name="get_certificate", annotations=READ)(self.get_certificate)

        if self.allow_write:
            self.mcp.tool(name="import_certificate", annotations=WRITE)(self.import_certificate)
            self.mcp.tool(name="delete_certificate", annotations=DESTRUCTIVE)(
                self.delete_certificate
            )

    async def list_certificates(
        self,
        name: str | None = Field(None, description="Filter by name."),
        page: int = Field(1, ge=1, description="Page number (1-based, as the API documents)."),
        size: int = Field(DEFAULT_PAGE_SIZE, ge=1, description="Page size."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vLB."),
    ) -> CertificateListData:
        """List the certificates available to this project.

        Returns {region, certificates[{id, name, type, domain_name, not_after,
        in_use, ...}], total_item, page, page_size}.

        ## Workflow
        - Pick the `id` of a 'TLS/SSL' certificate for a listener's
          `defaultCertificateAuthority`.
        - Check `not_after` before relying on one, and `in_use` before deleting.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region
        params: dict = {"page": page, "size": size}
        if name:
            params["name"] = name

        async def fetch() -> CertificateListData:
            raw = await self.client.get(f"/v2/{pid}/cas", region=region, params=params)
            envelope = raw if isinstance(raw, dict) else {}
            return CertificateListData(
                region=resolved_region,
                certificates=[
                    CertificateItem.from_api(c) for c in as_list(raw) if isinstance(c, dict)
                ],
                total_item=total_items(envelope),
                page=envelope.get("page"),
                page_size=envelope.get("pageSize"),
            )

        key = ("list_certificates", resolved_region, pid, name, page, size)
        return await self.cache.get_or_fetch("list_certificates", key, fetch, refresh)

    async def get_certificate(
        self,
        certificate_id: str = Field(..., description="Certificate ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CertificateItem:
        """Get one certificate by ID.

        Returns {id, name, type, domain_name, subject_alternative_names, issuer,
        not_before, not_after, in_use, ...}. The private key is never returned
        by the API and never by this tool.
        """
        validate_id(certificate_id, "certificate_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/cas/{certificate_id}", region=region)
        return CertificateItem.from_api(unwrap_one(data))

    async def import_certificate(
        self,
        body: ImportCertificateDto = Field(..., description="Certificate material and metadata."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> CertificateItem:
        """Import a certificate so listeners can serve it.

        ## Requirements
        - Requires `--allow-write`.
        - The `name` pattern here forbids underscores, unlike every other name
          in this API: letters, digits, '.' and '-' only, 5-50 characters.
        - `type` is 'TLS/SSL' for a server certificate or 'CA' for a
          certificate authority — note the slash in the first value.
        - The `privateKey` is secret material. It is sent once and never
          returned; do not echo it back to the user or into any other tool.
        - Ask the user for the PEM contents rather than reading key files on
          your own initiative.

        ## Workflow
        - Attach the returned `id` to a listener via
          `defaultCertificateAuthority` (create_listener / update_listener).
        """
        require_write(self.allow_write)
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/cas", region=region, json=payload)
        self.cache.invalidate("list_certificates")
        return CertificateItem.from_api(unwrap_one(data))

    async def delete_certificate(
        self,
        certificate_id: str = Field(..., description="Certificate ID."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a certificate.

        ## Requirements
        - Requires `--allow-write`.
        - Irreversible: the private key is gone and cannot be re-derived. If the
          certificate is still needed it must be imported again from the
          original PEM.
        - Check `in_use` with `get_certificate` first — deleting one a listener
          serves breaks TLS on that listener.

        ## Workflow
        - The API answers 204; confirm with `list_certificates`.
        """
        require_write(self.allow_write)
        validate_id(certificate_id, "certificate_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/cas/{certificate_id}", region=region)
        self.cache.invalidate("list_certificates")
        return f"Certificate {certificate_id} deleted."
