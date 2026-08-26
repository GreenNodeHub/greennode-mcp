"""System-image discovery for the vServer MCP server.

vServer splits its bootable images into two catalogues behind separate paths:
``/v1/{pid}/images/os`` for regular OS images and ``/v1/{pid}/images/gpu`` for
GPU-optimised ones — mirroring `grn vserver image list --type os|gpu`.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import ImageItem, ImageListData
from greennode.vserver_mcp_server.paging import as_list
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


class ImageHandler:
    """Register and serve image-discovery MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_images", annotations=READ)(self.list_images)

    async def list_images(
        self,
        image_type: Literal["os", "gpu"] = Field(
            "os",
            description=(
                "Which catalogue to list: 'os' for regular Linux/Windows images, "
                "'gpu' for GPU-optimised images. A GPU flavor needs a 'gpu' image."
            ),
        ),
        name_filter: str | None = Field(
            None,
            description=(
                "Optional case-insensitive substring matched against the OS family and "
                "version, e.g. 'ubuntu' or '22.04'. Filtering happens server-side in "
                "this tool because the API has no search parameter."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> ImageListData:
        """List the bootable system images available in a region.

        Returns {region, image_type, images[{id, image_type, image_version,
        licence, description}]}. `image_type` is the OS family (Ubuntu, CentOs,
        Debian, Windows, Redhat, Oracle, OpenSUSE, Other); `image_version` is
        the concrete build to show the user. `licence=true` means the image
        carries a paid OS licence that is billed on top of the instance.

        ## Workflow
        - Part of the create_server flow: present the matching images and let the
          user choose. IMPORTANT: do NOT pick an image silently.
        - The catalogue is long (45+ entries) — use `name_filter` when the user
          already named an OS instead of dumping everything.
        - Cross-check the chosen image's `image_type` against the flavor's
          `supported_image_types` (from list_flavors); the API rejects a
          mismatched pair at create time.
        - Use the chosen `id` as `imageId` in create_server.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[ImageItem]:
            raw = await self.client.get(f"/v1/{pid}/images/{image_type}", region=region)
            return [ImageItem.from_api(i) for i in as_list(raw, "images")]

        key = ("list_images", resolved_region, pid, image_type)
        images = await self.cache.get_or_fetch("list_images", key, fetch, refresh)

        if name_filter:
            needle = name_filter.strip().lower()
            images = [
                i
                for i in images
                if needle in i.image_version.lower() or needle in i.image_type.lower()
            ]

        return ImageListData(region=resolved_region, image_type=image_type, images=images)
