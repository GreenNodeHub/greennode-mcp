"""Listener response models.

Name drift handled here: ``listenerName``/``listenerProtocol``/
``listenerProtocolPort`` on the request side arrive as ``name``/``protocol``/
``protocolPort``, and ``clientCertificate`` comes back as
``clientCertificateAuthentication``. Model fields keep the request vocabulary.

``allowedCidrs``/``blockedCidrs`` are single comma-separated strings on the
wire in both directions; they are exposed as lists because that is what a
reader can act on.

``headers`` and ``insertHeaders`` are **different things** — the former is the
set of header names this listener may insert, the latter the injections
actually configured — so they are modelled separately rather than merged.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id, csv_to_list
from greennode.vlb_mcp_server.models.requests import HeaderDto
from pydantic import BaseModel, Field


class ListenerItem(BaseModel):
    """One listener."""

    id: str = Field(..., description="Listener ID — pass this as listener_id")
    name: str = Field("", description="Listener name (the API returns this as `name`)")
    protocol: str = Field(
        "", description="Listener protocol (request spells it `listenerProtocol`)"
    )
    port: int | None = Field(
        None, description="Listener port (the API returns this as `protocolPort`)"
    )
    description: str = Field("", description="Description")
    default_pool_id: str = Field("", description="Pool traffic goes to when no L7 policy matches")
    default_pool_name: str = Field("", description="Name of that pool")
    default_action: str = Field("", description="'accept' or 'drop' for unmatched traffic")
    timeout_client: int | None = Field(None, description="Client idle timeout (seconds)")
    timeout_member: int | None = Field(None, description="Member idle timeout (seconds)")
    timeout_connection: int | None = Field(None, description="Connection idle timeout (seconds)")
    connection_limit: int | None = Field(None, description="Concurrent connection limit")
    allowed_cidrs: list[str] = Field(
        default_factory=list, description="Source CIDRs allowed (stored as one string upstream)"
    )
    blocked_cidrs: list[str] = Field(
        default_factory=list, description="Source CIDRs blocked (stored as one string upstream)"
    )
    inserted_header_names: list[str] = Field(
        default_factory=list,
        description=(
            "The names from `insert_headers`, which is all the API's `headers` field "
            "turns out to hold — it is NOT the set of headers a listener may insert. "
            "Use list_listener_headers for that. Kept because the API sends it, but "
            "`insert_headers` carries the same information with the values attached."
        ),
    )
    insert_headers: list[HeaderDto] = Field(
        default_factory=list, description="Headers actually injected into forwarded requests"
    )
    certificate_authorities: list[str] = Field(
        default_factory=list, description="Certificates offered for SNI"
    )
    default_certificate_authority: str = Field(
        "", description="Certificate served when SNI matches nothing"
    )
    client_certificate: str = Field(
        "",
        description=(
            "Certificate used to verify client certificates (mTLS); the API returns "
            "this as clientCertificateAuthentication"
        ),
    )
    alpn_protocols: list[str] = Field(
        default_factory=list, description="ALPN protocols advertised during the TLS handshake"
    )
    tls_security_policy: str = Field("", description="Cipher/protocol policy for TLS")
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "ListenerItem":
        """Build a ListenerItem from a raw ListenerDto."""
        inserted = data.get("insertHeaders")
        available = data.get("headers")
        cas = data.get("certificateAuthorities")
        alpn = data.get("alpnProtocols")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            protocol=data.get("protocol") or "",
            port=data.get("protocolPort"),
            description=data.get("description") or "",
            default_pool_id=data.get("defaultPoolId") or "",
            default_pool_name=data.get("defaultPoolName") or "",
            default_action=data.get("defaultAction") or "",
            timeout_client=data.get("timeoutClient"),
            timeout_member=data.get("timeoutMember"),
            timeout_connection=data.get("timeoutConnection"),
            connection_limit=data.get("connectionLimit"),
            allowed_cidrs=csv_to_list(data.get("allowedCidrs")),
            blocked_cidrs=csv_to_list(data.get("blockedCidrs")),
            inserted_header_names=[
                str(h) for h in (available if isinstance(available, list) else [])
            ],
            insert_headers=[
                HeaderDto(headerName=h.get("headerName", ""), headerValue=h.get("headerValue", ""))
                for h in (inserted if isinstance(inserted, list) else [])
                if isinstance(h, dict)
            ],
            certificate_authorities=[str(c) for c in (cas if isinstance(cas, list) else [])],
            default_certificate_authority=data.get("defaultCertificateAuthority") or "",
            client_certificate=data.get("clientCertificateAuthentication") or "",
            alpn_protocols=[str(a) for a in (alpn if isinstance(alpn, list) else [])],
            tls_security_policy=data.get("tlsSecurityPolicy") or "",
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
            created_at=str(data.get("createdAt") or ""),
            updated_at=str(data.get("updatedAt") or ""),
        )


class ListenerListData(BaseModel):
    """Structured response for list_listeners."""

    load_balancer_id: str = Field(..., description="Load balancer the listeners belong to")
    listeners: list[ListenerItem] = Field(
        default_factory=list, description="Listeners of the load balancer"
    )
