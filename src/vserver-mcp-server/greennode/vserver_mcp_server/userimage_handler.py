"""User image, tag and quota tools for the vServer MCP server.

User images are the custom images captured from servers; tags are managed
through one generic endpoint shared by every resource family; quota reports
what the project may consume. Mirrors `grn vserver user-image` and the
`server tag-key` / `tag-value` commands.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    QuotaItem,
    QuotaListData,
    ResourceTagItem,
    ResourceTagListData,
    TagItem,
    TagListData,
    UpdateResourceTagsDto,
    UserImageItem,
    UserImageListData,
)
from greennode.vserver_mcp_server.paging import as_list, fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id, validate_path_segment
from pydantic import Field


class UserImageHandler:
    """Register and serve user-image, tag and quota MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_user_images", annotations=READ)(self.list_user_images)
        self.mcp.tool(name="get_user_image", annotations=READ)(self.get_user_image)
        self.mcp.tool(name="list_tag_keys", annotations=READ)(self.list_tag_keys)
        self.mcp.tool(name="list_tag_values", annotations=READ)(self.list_tag_values)
        self.mcp.tool(name="list_resource_tags", annotations=READ)(self.list_resource_tags)
        self.mcp.tool(name="get_quota", annotations=READ)(self.get_quota)
        self.mcp.tool(name="list_tags", annotations=READ)(self.list_tags)
        self.mcp.tool(name="get_tag_quota", annotations=READ)(self.get_tag_quota)

        if self.allow_write:
            self.mcp.tool(name="update_resource_tags", annotations=WRITE)(
                self.update_resource_tags
            )
            self.mcp.tool(name="delete_user_image", annotations=DESTRUCTIVE)(
                self.delete_user_image
            )

    async def list_user_images(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the image name."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> UserImageListData:
        """List the user images (custom images captured from servers).

        Returns {region, user_images[{id, name, status, size_gb, created_at}]}.
        Each image consumes billable storage until it is deleted.

        ## Workflow
        - A user image `id` can be passed as `imageId` to create_server, which
          is how you clone a configured machine.
        """
        pid = await require_project_id(self.config, self.client, region)
        params = {"name": name_filter} if name_filter else None
        raw = await fetch_all_items(
            self.client, f"/v2/{pid}/user-images", region=region, params=params
        )
        return UserImageListData(
            region=region or self.config.default_region,
            user_images=[UserImageItem.from_api(i) for i in raw],
        )

    async def get_user_image(
        self,
        user_image_id: str = Field(..., description="User image ID from list_user_images."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> UserImageItem:
        """Get one user image by id.

        Use it to poll `status` after create_server_image until the capture has
        finished and the image can be used in create_server.
        """
        validate_id(user_image_id, "user_image_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/user-images/{user_image_id}", region=region)
        return UserImageItem.from_api(unwrap(data) or {})

    async def delete_user_image(
        self,
        user_image_id: str = Field(..., description="User image ID from list_user_images."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a user image. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Servers already created from the image keep running, but the image
          can no longer be used for new ones.

        ## Workflow
        - Show the user the image's id, name and size and get explicit
          confirmation. Deleting it does stop its storage charge.
        """
        require_write(self.allow_write)
        validate_id(user_image_id, "user_image_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/user-images/{user_image_id}", region=region)
        return f"User image {user_image_id} deleted."

    async def list_tag_keys(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ResourceTagListData:
        """List every tag key used in the project.

        ## Workflow
        - Call this before tagging a resource so you reuse an existing key
          instead of inventing a near-duplicate ("env" vs "environment").
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/tag/tag-key", region=region)
        return ResourceTagListData(values=_tag_strings(data, "key"))

    async def list_tag_values(
        self,
        key: str = Field(..., description="Tag key from list_tag_keys."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ResourceTagListData:
        """List the values already used for one tag key.

        Platform keys are dotted (`vng.vpc.id`), so pass the key exactly as
        list_tag_keys returned it.
        """
        validate_path_segment(key, "key")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/tag/tag-key/{key}/tag-value", region=region)
        return ResourceTagListData(values=_tag_strings(data, "value"))

    async def list_resource_tags(
        self,
        resource_id: str = Field(
            ..., description="ID of any taggable resource (server, volume, image, interface)."
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> list[ResourceTagItem]:
        """List the tags currently attached to one resource.

        ## Workflow
        - Always call this before update_resource_tags: that tool replaces the
          whole tag list, so you need the current entries to keep the ones the
          user did not mean to remove.
        - Keys starting `vng.` are platform tags. They survive an update on
          their own, may appear more than once here, and must **not** be
          resent — see update_resource_tags.
        """
        validate_id(resource_id, "resource_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/tag/resource/{resource_id}", region=region)
        return [ResourceTagItem.from_api(t) for t in as_list(data)]

    async def update_resource_tags(
        self,
        resource_id: str = Field(..., description="ID of the resource to tag."),
        body: UpdateResourceTagsDto = Field(..., description="Complete replacement tag list."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> list[ResourceTagItem]:
        """Replace the tags of any resource.

        vServer tags every resource family through this one endpoint rather
        than through the resource's own API, so `resourceType` says what kind
        of id was passed.

        ## Requirements
        - Requires `--allow-write`.
        - `tagRequestList` is a **full replacement** of the user tags: any of
          them missing from it is removed, and an empty list clears them all.
          Call list_resource_tags first and resend the ones to keep.
        - **Never resend a `vng.*` tag.** The platform owns those, re-applies
          them by itself, and rejects the values it wrote (`vng.createdBy`
          holds a colon, which the input validation forbids).
        - A tag value must be **3-255 characters**; keys and values take
          letters, digits and `_ . @ -` only.
        - Mark entries whose value you changed with `isEdited=true`; leave it
          false for untouched ones.
        - `resourceType` must match the resource family, e.g. NETWORK-INTERFACE,
          SERVER, VOLUME, USER-IMAGE.
        """
        require_write(self.allow_write)
        validate_id(resource_id, "resource_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        payload["resourceId"] = resource_id
        data = await self.client.put(
            f"/v2/{pid}/tag/resource/{resource_id}", region=region, json=payload
        )
        return [ResourceTagItem.from_api(t) for t in as_list(data)]

    async def get_quota(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> QuotaListData:
        """Get the project's resource quota and current usage.

        Returns {region, quotas[{name, type, limit, used, description}]} for
        every quota line the platform tracks — servers, volumes, SSH keys,
        routes, IPs and more.

        ## Workflow
        - Call this before create_server, create_volume or create_ssh_key to
          catch a full quota early instead of failing mid-create.
        - Quota is per region: check the region the resource will live in.
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/quotas/quotaUsed", region=region)
        return QuotaListData(
            region=region or self.config.default_region,
            quotas=[QuotaItem.from_api(q) for q in as_list(data)],
        )

    async def list_tags(
        self,
        include_system: bool = Field(
            False,
            description=(
                "Include platform-managed tags such as vng.serverId. Off by default "
                "because they are noise for a user reviewing their own tagging."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> TagListData:
        """List every tag defined in the project.

        Returns {region, tags[{id, key, value, system, resource_type,
        created_at}]}.

        list_tag_keys and list_tag_values give the key/value vocabulary; this
        gives the actual tag objects, system tags included.

        ## Workflow
        - Use it to audit tagging across the project — for example to find which
          key/value pairs cost centres actually use before standardising.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/tag", region=region)
        tags = [TagItem.from_api(t) for t in raw]
        if not include_system:
            tags = [t for t in tags if not t.system]
        return TagListData(region=region or self.config.default_region, tags=tags)

    async def get_tag_quota(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> QuotaItem:
        """Get how many tags one resource may carry.

        Returns a single quota line (name, limit, used, description).
        update_resource_tags replaces a resource's whole tag list, so this is
        the ceiling that call has to stay under.
        """
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/tag/quota", region=region)
        return QuotaItem.from_api(unwrap(data) or {})


def _tag_strings(data: object, field: str) -> list[str]:
    """Flatten a tag-key or tag-value response into plain strings.

    The endpoints return either bare strings or objects carrying the value
    under *field*, depending on the deployment.
    """
    out: list[str] = []
    for item in as_list(data):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            value = item.get(field) or item.get("name")
            if value:
                out.append(str(value))
    return out
