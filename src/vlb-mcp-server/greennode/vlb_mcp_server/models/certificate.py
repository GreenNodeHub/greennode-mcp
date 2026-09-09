"""Certificate response models.

The API calls this resource ``cas`` and its DTO ``CADto``; the tools call it a
certificate, which is what it is in every user-facing sense.

``CADto`` carries three different date representations — ``expiredAt`` as a
plain string, ``notBefore``/``notAfter`` as epoch integers, ``importedAt`` as a
timestamp — so all of them are surfaced as strings rather than pretending to a
single normalised type.

Nothing here exposes a private key: the import DTO accepts one, but it never
travels back out through a model.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id, epoch_to_iso
from pydantic import BaseModel, Field


class CertificateItem(BaseModel):
    """One certificate."""

    id: str = Field(..., description="Certificate ID — pass this as a listener's certificate")
    name: str = Field("", description="Certificate name")
    type: str = Field(
        "", description="'TLS/SSL' or 'CA' (the API returns this as certificateType)"
    )
    domain_name: str = Field("", description="Primary domain the certificate covers")
    subject: str = Field("", description="Certificate subject")
    subject_alternative_names: list[str] = Field(
        default_factory=list, description="Additional domains the certificate covers"
    )
    issuer: str = Field("", description="Issuing authority")
    serial: str = Field("", description="Serial number")
    key_algorithm: str = Field("", description="Key algorithm")
    signature_algorithm: str = Field("", description="Signature algorithm")
    expired_at: str = Field("", description="Expiry as the API reports it (plain string)")
    not_before: str = Field(
        "", description="Validity start as ISO-8601 UTC (the API reports epoch millis)"
    )
    not_after: str = Field(
        "",
        description=(
            "Expiry as ISO-8601 UTC (the API reports epoch millis). This is the field to "
            "answer 'when does this certificate expire'; `expired_at` is usually empty."
        ),
    )
    imported_at: str = Field("", description="When the certificate was imported")
    in_use: bool = Field(False, description="Whether a listener currently serves it")

    @classmethod
    def from_api(cls, data: dict) -> "CertificateItem":
        """Build a CertificateItem from a raw CADto."""
        sans = data.get("subjectAlternativeNames")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            type=data.get("certificateType") or "",
            domain_name=data.get("domainName") or "",
            subject=data.get("subject") or "",
            subject_alternative_names=[str(s) for s in (sans if isinstance(sans, list) else [])],
            issuer=data.get("issuer") or "",
            serial=data.get("serial") or "",
            key_algorithm=data.get("keyAlgorithm") or "",
            signature_algorithm=data.get("signatureAlgorithm") or "",
            expired_at=str(data.get("expiredAt") or ""),
            not_before=epoch_to_iso(data.get("notBefore")),
            not_after=epoch_to_iso(data.get("notAfter")),
            imported_at=str(data.get("importedAt") or ""),
            in_use=bool(data.get("inUse")),
        )


class CertificateListData(BaseModel):
    """Structured response for list_certificates."""

    region: str = Field(..., description="Region the certificates were fetched from")
    certificates: list[CertificateItem] = Field(
        default_factory=list, description="Certificates in the project"
    )
    total_item: int | None = Field(None, description="Total the gateway reports for this query")
    page: int | None = Field(None, description="Page returned (1-based)")
    page_size: int | None = Field(None, description="Page size returned")
