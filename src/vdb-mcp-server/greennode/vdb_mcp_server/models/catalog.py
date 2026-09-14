"""Catalogue models: what the platform offers, as opposed to what the account owns.

Every model here is a projection. The raw catalogue rows carry pricing keys,
internal zone UUIDs and SKU strings that no caller needs in order to choose an
engine or a flavour; the fields kept are the ones a create actually consumes,
plus enough description to choose between rows.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatastoreOption(BaseModel):
    """One engine + version pair the platform can provision."""

    name: str = Field("", description="Display name, e.g. 'MySQL'")
    type: str = Field(
        "", description="Engine type as the API expects it in `type` (lowercase, e.g. 'mysql')"
    )
    version: str = Field("", description="Version as the API expects it in `version`, e.g. '8.0'")
    version_name: str = Field("", description="Version display name")
    license_name: str = Field("", description="Licence, e.g. 'Community Edition'")

    @classmethod
    def from_api(cls, data: dict) -> "DatastoreOption":
        """Build from a raw `EngineVersion` row."""
        return cls(
            name=data.get("name") or "",
            type=data.get("type") or "",
            version=data.get("version") or "",
            version_name=data.get("versionName") or "",
            license_name=data.get("licenseName") or "",
        )


class EngineOption(BaseModel):
    """One engine family (without its versions)."""

    name: str = Field("", description="Engine name")
    description: str = Field("", description="Engine description")
    licenses: list[str] = Field(default_factory=list, description="Available licence names")

    @classmethod
    def from_api(cls, data: dict) -> "EngineOption":
        """Build from a raw `Engine` row."""
        raw_licenses = data.get("engineLicenses") or []
        licenses = [
            item.get("name", "") if isinstance(item, dict) else str(item) for item in raw_licenses
        ]
        return cls(
            name=data.get("name") or "",
            description=data.get("description") or "",
            licenses=[lic for lic in licenses if lic],
        )


class InstanceFamilyOption(BaseModel):
    """One row of the families endpoint -- which returns two different kinds of row.

    `group` is the discriminator:

    * ``family_custom`` rows describe a **custom zone**, not a family: ``id``
      is the zone group UUID that a flavour reports as ``zone_group_id`` and
      ``name`` is its label ("ENG DEV 1a"); ``key``/``value`` are empty.
    * every other group is a real instance family: ``key`` is the
      ``family_type`` carried by each flavour, and ``codes`` lists the platform
      codes (``code-s``, ``code-a``, ...) that belong to it.
    """

    id: str = Field("", description="Family or zone-group ID")
    name: str = Field("", description="Display name")
    key: str = Field("", description="Family key matching a flavour's family_type")
    value: str = Field("", description="Family display value")
    group: str = Field(
        "", description="Row kind; 'family_custom' means this row describes a zone group"
    )
    description: str = Field("", description="Description")
    codes: list[str] = Field(
        default_factory=list, description="Platform codes belonging to this family"
    )

    @classmethod
    def from_api(cls, data: dict) -> "InstanceFamilyOption":
        """Build from a raw `InstanceFamily` row."""
        condition = data.get("condition")
        codes = condition.get("codes") if isinstance(condition, dict) else None
        return cls(
            id=str(data.get("id") or ""),
            name=data.get("name") or "",
            key=data.get("key") or "",
            value=str(data.get("value") or ""),
            group=data.get("group") or "",
            description=data.get("description") or "",
            codes=[str(c) for c in (codes or [])],
        )


class FlavorOption(BaseModel):
    """One purchasable instance size for a given engine, version and zone."""

    id: str = Field(
        "",
        description=(
            "Flavour ID as the row reports it. Relational and memory creates take this; a "
            "Kafka create does NOT -- it wants flavor_id."
        ),
    )
    flavor_id: str = Field(
        "",
        description=(
            "Kafka's own flavour id ('flav-…'), which is what a Kafka create's "
            "serverFlavorId takes. Empty for the other families, which do not publish one."
        ),
    )
    name: str = Field("", description="Flavour name, e.g. 'db.s-general-2x4'")
    description: str = Field("", description="Flavour description")
    vcpus: int | None = Field(None, description="vCPU count")
    ram_gb: int | None = Field(None, description="RAM in GB")
    default_volume_size_gb: int | None = Field(None, description="Default volume size in GB")
    default_volume_type: str = Field("", description="Default volume type")
    zone_id: str = Field("", description="Availability zone, e.g. 'HCM03-1A'")
    zone_group_id: str = Field(
        "", description="Zone group UUID; matches a 'family_custom' row of the families endpoint"
    )
    package_sku: str = Field("", description="Billing SKU")
    family_type: str = Field("", description="Instance family key")
    platform_type: str = Field("", description="Platform code, e.g. 'code-a'")
    monthly_cost: float | None = Field(None, description="Indicative monthly cost")

    @classmethod
    def from_api(cls, data: dict) -> "FlavorOption":
        """Build from a raw `FlavorInfo` row.

        Three fields look like the zone and only one is: ``locateZoneId`` holds
        the availability zone (``HCM03-1A``) that matches `list_relational_zones`
        and the `zone_id` argument. ``zoneId`` is an internal integer index and
        ``zoneUUID`` identifies a zone *group*, so neither is usable as a zone.
        """
        return cls(
            id=str(data.get("id") or ""),
            flavor_id=str(data.get("flavorId") or ""),
            name=data.get("name") or "",
            description=data.get("description") or "",
            vcpus=data.get("vcpus"),
            ram_gb=data.get("ram"),
            default_volume_size_gb=data.get("volumeSize"),
            default_volume_type=data.get("volumeType") or "",
            zone_id=str(data.get("locateZoneId") or ""),
            zone_group_id=str(data.get("zoneUUID") or ""),
            package_sku=data.get("packageSku") or "",
            family_type=data.get("familyType") or "",
            platform_type=data.get("platformType") or "",
            monthly_cost=data.get("monthlyCost"),
        )


class FlavorCodeOption(BaseModel):
    """One flavour code (the short key behind a family)."""

    key: str = Field("", description="Code key")
    value: str = Field("", description="Code value")
    description: str = Field("", description="Code description")
    family_type: str = Field("", description="Instance family this code belongs to")

    @classmethod
    def from_api(cls, data: dict) -> "FlavorCodeOption":
        """Build from a raw `FlavorCode` row."""
        return cls(
            key=data.get("key") or "",
            value=str(data.get("value") or ""),
            description=data.get("description") or "",
            family_type=data.get("familyType") or "",
        )


class VolumeTypeOption(BaseModel):
    """One storage type, with the size range it accepts."""

    id: str = Field(
        "",
        description=(
            "Volume type ID as the row reports it. The PostgreSQL Cluster create takes this; "
            "a Kafka create does NOT -- it wants kafka_uuid."
        ),
    )
    kafka_uuid: str = Field(
        "",
        description=(
            "Kafka's own volume type id ('vtype-…'), which is what a Kafka create's "
            "kafkaStorageType and update_kafka_cluster_storage_type take. Empty for the "
            "other families, which do not publish one."
        ),
    )
    type: str = Field(
        "",
        description=(
            "Volume type name. The relational create takes this; the PostgreSQL Cluster and "
            "Kafka creates take an id instead."
        ),
    )
    description: str = Field("", description="Volume type description")
    iops: int | None = Field(None, description="Provisioned IOPS")
    min_volume_size: int | None = Field(None, description="Smallest volume size in GB")
    max_volume_size: int | None = Field(None, description="Largest volume size in GB")
    zone_id: str = Field("", description="Availability zone")
    volume_type_zone_id: str = Field("", description="Zone-scoped volume type ID used by creates")

    @classmethod
    def from_api(cls, data: dict) -> "VolumeTypeOption":
        """Build from a raw `VolumeTypeInfo` row."""
        return cls(
            id=str(data.get("id") or ""),
            kafka_uuid=str(data.get("kafkaUuid") or ""),
            type=data.get("type") or "",
            description=data.get("description") or "",
            iops=data.get("iops"),
            min_volume_size=data.get("minVolumeSize"),
            max_volume_size=data.get("maxVolumeSize"),
            zone_id=data.get("zoneId") or "",
            volume_type_zone_id=data.get("volumeTypeZoneId") or "",
        )


class ZoneOption(BaseModel):
    """One availability zone."""

    id: str = Field("", description="Zone ID as creates and filters expect it")
    name: str = Field("", description="Zone name")
    status: str = Field("", description="Zone status")
    zone_type: str = Field("", description="Zone type")
    is_default: bool = Field(False, description="Whether this is the default zone")
    description: str = Field("", description="Zone description")

    @classmethod
    def from_api(cls, data: dict) -> "ZoneOption":
        """Build from a raw `ZoneInfo` row."""
        return cls(
            id=data.get("uuid") or "",
            name=data.get("name") or "",
            status=data.get("status") or "",
            zone_type=data.get("zoneType") or "",
            is_default=bool(data.get("isDefault")),
            description=data.get("description") or "",
        )


class NetworkOption(BaseModel):
    """One network (VPC) a database instance can be placed in."""

    id: str = Field("", description="Network ID")
    name: str = Field("", description="Network display name")
    cidr: str = Field("", description="Network CIDR")
    status: str = Field("", description="Network status")

    @classmethod
    def from_api(cls, data: dict) -> "NetworkOption":
        """Build from a raw `NetworkResponse` row."""
        return cls(
            id=data.get("id") or data.get("uuid") or "",
            name=data.get("displayName") or "",
            cidr=data.get("cidr") or "",
            status=data.get("status") or "",
        )


class SubnetOption(BaseModel):
    """One subnet, flattened out of the network that owns it.

    A create takes a ``subnetId``, but the API answers with networks that
    *contain* subnets, so the parent network travels on each row rather than
    being lost.
    """

    subnet_id: str = Field("", description="Subnet ID -- what a create expects")
    name: str = Field("", description="Subnet name")
    cidr: str = Field("", description="Subnet CIDR")
    status: str = Field("", description="Subnet status")
    zone_id: str = Field("", description="Availability zone")
    network_id: str = Field("", description="ID of the network that owns this subnet")
    network_name: str = Field("", description="Display name of the owning network")

    @classmethod
    def from_network(cls, network: dict, subnet: dict) -> "SubnetOption":
        """Build one row from a network and one of its subnets."""
        return cls(
            subnet_id=subnet.get("uuid") or "",
            name=subnet.get("name") or "",
            cidr=subnet.get("cidr") or "",
            status=subnet.get("status") or "",
            zone_id=subnet.get("zoneId") or network.get("zoneId") or "",
            network_id=network.get("uuid") or "",
            network_name=network.get("displayName") or "",
        )


class _CatalogListData(BaseModel):
    """Shared envelope for a catalogue answer."""

    count: int = Field(0, description="Number of options returned")
    note: str | None = Field(
        None,
        description="Set when the result needs explaining, e.g. an empty list with a likely cause",
    )


class DatastoreListData(_CatalogListData):
    """Engine + version pairs the platform can provision."""

    items: list[DatastoreOption] = Field(default_factory=list, description="Datastore options")


class EngineListData(_CatalogListData):
    """Engine families."""

    items: list[EngineOption] = Field(default_factory=list, description="Engine options")


class InstanceFamilyListData(_CatalogListData):
    """Instance families."""

    items: list[InstanceFamilyOption] = Field(
        default_factory=list, description="Instance family options"
    )


class FlavorListData(_CatalogListData):
    """Instance sizes for one engine, version and (optionally) zone."""

    datastore_type: str = Field("", description="Engine type the flavours were requested for")
    version: str = Field("", description="Version the flavours were requested for")
    zone_id: str | None = Field(
        None, description="Zone the result describes; None means the platform default zone"
    )
    items: list[FlavorOption] = Field(default_factory=list, description="Flavour options")


class FlavorCodeListData(_CatalogListData):
    """Flavour codes."""

    items: list[FlavorCodeOption] = Field(default_factory=list, description="Flavour code options")


class VolumeTypeListData(_CatalogListData):
    """Storage types and the size ranges they accept, for one zone."""

    zone_id: str | None = Field(
        None, description="Zone the result describes; None means the platform default zone"
    )
    items: list[VolumeTypeOption] = Field(default_factory=list, description="Volume type options")


class ZoneListData(_CatalogListData):
    """Availability zones."""

    items: list[ZoneOption] = Field(default_factory=list, description="Zone options")


class NetworkListData(_CatalogListData):
    """Networks a database instance can be placed in."""

    items: list[NetworkOption] = Field(default_factory=list, description="Network options")


class SubnetListData(_CatalogListData):
    """Subnets, flattened out of their networks."""

    items: list[SubnetOption] = Field(default_factory=list, description="Subnet options")
