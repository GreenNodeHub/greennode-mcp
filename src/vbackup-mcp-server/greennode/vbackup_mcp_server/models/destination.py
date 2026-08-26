"""Models for the backup-destination sub-resources and the create-time lookups.

The console calls a destination a **Backup Location** and its detail page is
built from four of these: the vServer resources stored there, the vDB resources
stored there, the tags on it, and its own change history. The products and
regions lookups exist only to answer what a create may put in ``product`` and
``regionId``.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_text
from pydantic import BaseModel, Field


class ProductItem(BaseModel):
    """One GreenNode product vBackup can protect."""

    id: str = Field(..., description="Product ID (`prd-...`)")
    product: str = Field(
        "",
        description=(
            "The value a create body puts in `product` — 'vServer' or 'vDB'. This "
            "string, not the id, is what the API expects."
        ),
    )
    enabled: bool = Field(
        True, description="False when the platform has withdrawn backup support for it"
    )

    @classmethod
    def from_api(cls, data: dict) -> ProductItem:
        """Build a ProductItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            product=as_text(data.get("product")),
            enabled=bool(data.get("enabled")),
        )


class ProductListData(BaseModel):
    """Structured output of list_backup_products."""

    region: str = Field(..., description="Region the gateway was called in")
    total: int = Field(0, description="Number of products returned")
    products: list[ProductItem] = Field(
        default_factory=list, description="Products vBackup can protect"
    )


class BackupRegionItem(BaseModel):
    """One backup region a destination of a given product may be placed in.

    These are the **storage** regions vBackup offers, named after the physical
    site (HCM04, HAN02). They are not the two API gateways this server routes
    to (`HCM-3`, `HAN`) and they do not have to match: a destination created
    through the HCM-3 gateway can store its data in HAN02, which is how a
    cross-region backup is set up.
    """

    id: str = Field(..., description="Configuration ID of the region entry (`vst-cf...`)")
    name: str = Field("", description="Region name shown in the console, e.g. HCM04")
    region_id: str = Field(
        "",
        description=(
            "The value create_backup_destination puts in `regionId`. This is NOT "
            "`id` — sending `id` instead is rejected."
        ),
    )
    product: str = Field("", description="Product this region entry serves")

    @classmethod
    def from_api(cls, data: dict) -> BackupRegionItem:
        """Build a BackupRegionItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            name=as_text(data.get("name")),
            region_id=as_text(data.get("regionId")),
            product=as_text(data.get("product")),
        )


class BackupRegionListData(BaseModel):
    """Structured output of list_backup_regions."""

    region: str = Field(..., description="Region the gateway was called in")
    product: str = Field("", description="Product the regions were listed for")
    total: int = Field(0, description="Number of backup regions returned")
    regions: list[BackupRegionItem] = Field(
        default_factory=list, description="Backup regions available for the product"
    )


class DestinationTagItem(BaseModel):
    """One tag on a backup destination."""

    key: str = Field("", description="Tag key")
    value: str = Field("", description="Tag value")
    resource_id: str = Field("", description="Resource the tag is attached to")
    resource_type: str = Field(
        "", description="Resource type as the API names it, e.g. BACKUP_LOCATION"
    )
    system_tag: bool = Field(
        False,
        description=(
            "True for a tag the platform set itself (`vng.*`). System tags record "
            "provenance and are not user-editable."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> DestinationTagItem:
        """Build a DestinationTagItem from a raw API dict."""
        return cls(
            key=as_text(data.get("key")),
            value=as_text(data.get("value")),
            resource_id=as_text(data.get("resourceId")),
            resource_type=as_text(data.get("resourceType")),
            system_tag=bool(data.get("systemTag")),
        )


class DestinationTagListData(BaseModel):
    """Structured output of list_backup_destination_tags."""

    region: str = Field(..., description="Region the gateway was called in")
    destination_id: str = Field("", description="Destination the tags belong to")
    total: int = Field(0, description="Number of tags returned")
    tags: list[DestinationTagItem] = Field(
        default_factory=list, description="Tags on the destination"
    )


class DestinationHistoryItem(BaseModel):
    """One change made to a backup destination."""

    id: str = Field(..., description="History record ID (`bk-des-his-...`)")
    destination_id: str = Field("", description="Destination the change was made to")
    destination_name: str = Field("", description="Destination name at the time of the change")
    action: str = Field(
        "",
        description=(
            "What was done: CREATE, DELETE, SWITCH_DEFAULT, EDIT_MAX_QUOTA, "
            "ENABLE_SOFT_DELETED, EDIT_SOFT_DELETED and the vault-lock equivalents."
        ),
    )
    status: str = Field("", description="SUCCESS or ERROR — a failed attempt is recorded too")
    error_message: str = Field(
        "",
        description=(
            "Why the attempt failed, empty on success. `backup_location_is_being_used` "
            "means the destination still had backup servers writing to it."
        ),
    )
    description: str = Field(
        "",
        description=(
            "The change in the API's own words, including the values used, e.g. "
            "'Edit max-quota with {max-quota: 150GB}'."
        ),
    )
    created_at: str = Field("", description="When the change was attempted")

    @classmethod
    def from_api(cls, data: dict) -> DestinationHistoryItem:
        """Build a DestinationHistoryItem from a raw API dict."""
        return cls(
            id=as_text(data.get("id")),
            destination_id=as_text(data.get("backupDestinationId")),
            destination_name=as_text(data.get("backupDestinationName")),
            action=as_text(data.get("action")),
            status=as_text(data.get("status")),
            error_message=as_text(data.get("errorMessage")),
            description=as_text(data.get("description")),
            created_at=as_text(data.get("createdAt")),
        )


class DestinationHistoryListData(BaseModel):
    """Structured output of list_backup_destination_history."""

    region: str = Field(..., description="Region the gateway was called in")
    destination_id: str = Field("", description="Destination the history belongs to")
    total: int = Field(0, description="Number of records returned")
    changes: list[DestinationHistoryItem] = Field(
        default_factory=list, description="Changes, newest first"
    )
