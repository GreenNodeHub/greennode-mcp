"""Project-certificate handler for the vMonitor MCP server (integration).

Client certificates authenticate agents that ship logs into a log project. They
belong to the integration capability (setting up a log source) rather than the
log-project tools, so they are grouped here — though the endpoints live on the
Log API (``VmonitorLogClient``).
"""

from __future__ import annotations

import base64
from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class CertificateHandler:
    """Register and serve vMonitor log-project certificate MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_project_certificate_download", annotations=READ)(
            self.get_project_certificate_download
        )

        if self.allow_write:
            self.mcp.tool(name="create_project_certificate", annotations=WRITE)(
                self.create_project_certificate
            )
            self.mcp.tool(name="delete_project_certificate", annotations=DESTRUCTIVE)(
                self.delete_project_certificate
            )

    async def get_project_certificate_download(
        self,
        project_id: str = Field(..., description="Log project ID the certificate belongs to"),
        cert_id: str = Field(
            ...,
            description=(
                "Certificate ID to download. Obtain it from list_projects — each "
                "project item carries a certInfos[] array of {certId, status, "
                "expireAt}; certId is this value."
            ),
        ),
    ) -> str:
        """Download a project's client certificate, returned as base64-encoded bytes.

        The endpoint returns a binary certificate bundle (not text), so the raw
        bytes are base64-encoded; decode the result to recover the file.
        """
        validate_id(project_id, "project_id")
        validate_id(cert_id, "cert_id")
        content = await self.client.get_bytes(
            f"/v1/downloads/certificates/projects/{project_id}/{cert_id}"
        )
        return base64.b64encode(content).decode("ascii")

    async def create_project_certificate(
        self,
        project_id: str = Field(..., description="Log project ID to issue a certificate for"),
    ) -> str:
        """Issue a new client certificate for a log project.

        Certificates authenticate agents shipping logs to the project; download
        it afterwards with get_project_certificate_download.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(project_id, "project_id")
        await self.client.post(f"/v1/projects/{project_id}/certificates")
        return f"Certificate issued for project {project_id}."

    async def delete_project_certificate(
        self,
        project_id: str = Field(..., description="Log project ID the certificate belongs to"),
        cert_id: str = Field(..., description="Certificate ID to revoke. IRREVERSIBLE."),
    ) -> str:
        """Revoke a log project's client certificate. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Agents using the certificate will stop being able to ship logs.
        """
        validate_id(project_id, "project_id")
        validate_id(cert_id, "cert_id")
        await self.client.delete(f"/v1/projects/{project_id}/certificates/{cert_id}")
        return f"Certificate {cert_id} revoked for project {project_id}."
