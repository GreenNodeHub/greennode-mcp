"""Project-ID resolution for the vServer API.

Almost every vServer path is project-scoped (``/v2/{projectId}/...``), and the
project is **region-scoped**: each region's gateway exposes a different one.
Every handler resolves it through :func:`require_project_id` instead of asking
the agent for it.
"""

from __future__ import annotations

from greennode.mcp_core.http import current_identity
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import VserverConfig
from greennode.vserver_mcp_server.paging import as_list


async def require_project_id(
    config: VserverConfig, client: VserverClient, region: str | None = None
) -> str:
    """Return the project_id for *region*, discovering it from vServer when unset.

    Resolution: for the default region, use the configured value
    (``GRN_PROJECT_ID`` / credentials file) when present; otherwise fetch
    ``GET /v1/projects`` at that region's endpoint. Results are cached per
    (caller identity, region) so later calls don't refetch.

    The configured project_id belongs to the **service account** and its
    **default region** only — a passthrough user must never silently inherit
    it, since their token resolves to their own project.
    """
    resolved_region = region or config.default_region
    identity = current_identity()

    if identity == "service" and resolved_region == config.default_region and config.project_id:
        return config.project_id

    cache_key = (identity, resolved_region)
    if cache_key in config.project_id_by_region:
        return config.project_id_by_region[cache_key]

    data = await client.get("/v1/projects", region=region)
    projects = as_list(data, "projects", "data", "listData")
    if not projects or not isinstance(projects[0], dict):
        raise ValueError(
            f"Could not determine project_id for region '{resolved_region}': vServer "
            "returned no project. Set GRN_PROJECT_ID or run 'grn configure'."
        )
    pid = projects[0].get("projectId") or projects[0].get("id")
    if not pid:
        raise ValueError("Could not determine project_id from the vServer response.")

    config.project_id_by_region[cache_key] = pid
    if identity == "service" and resolved_region == config.default_region:
        config.project_id = pid
    return pid
