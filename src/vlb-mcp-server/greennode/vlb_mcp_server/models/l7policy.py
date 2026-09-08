"""L7 policy and rule response models.

``L7RuleDto`` is the one DTO in this API that does **not** use the
``displayStatus``/``progressStatus`` pair — it reports ``provisioningStatus``
and ``operatingStatus`` instead. Both are surfaced under their own names rather
than forced into the shape every other resource uses.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.models._common import _resource_id
from pydantic import BaseModel, Field


class L7RuleItem(BaseModel):
    """One match condition of a policy."""

    id: str = Field("", description="Rule ID")
    rule_type: str = Field("", description="'PATH' or 'HOST_NAME'")
    compare_type: str = Field(
        "", description="CONTAINS / ENDS_WITH / EQUAL_TO / REGEX / STARTS_WITH"
    )
    rule_value: str = Field("", description="Value compared against")
    provisioning_status: str = Field(
        "", description="Provisioning state (rules use this instead of displayStatus)"
    )
    operating_status: str = Field(
        "", description="Operating state (rules use this instead of progressStatus)"
    )

    @classmethod
    def from_api(cls, data: dict) -> "L7RuleItem":
        """Build an L7RuleItem from a raw L7RuleDto."""
        return cls(
            id=_resource_id(data),
            rule_type=data.get("ruleType") or "",
            compare_type=data.get("compareType") or "",
            rule_value=data.get("ruleValue") or "",
            provisioning_status=data.get("provisioningStatus") or "",
            operating_status=data.get("operatingStatus") or "",
        )


class L7PolicyItem(BaseModel):
    """One L7 routing policy of a listener."""

    id: str = Field(..., description="Policy ID — pass this as policy_id")
    name: str = Field("", description="Policy name (permanent; update cannot change it)")
    action: str = Field("", description="'REDIRECT_TO_POOL' or 'REDIRECT_TO_URL'")
    redirect_pool_id: str = Field("", description="Target pool when redirecting to a pool")
    redirect_pool_name: str = Field("", description="Name of that pool")
    redirect_url: str = Field("", description="Target URL when redirecting to a URL")
    redirect_http_code: int | None = Field(None, description="301 or 302 for a URL redirect")
    keep_query_string: bool = Field(False, description="Whether the query string is carried over")
    position: int | None = Field(None, description="Evaluation order; lower is checked first")
    description: str = Field("", description="Description")
    rules: list[L7RuleItem] = Field(
        default_factory=list, description="Match conditions; ALL must match"
    )
    display_status: str = Field("", description="Lifecycle status")
    progress_status: str = Field("", description="In-flight operation, if any")
    created_at: str = Field("", description="Creation timestamp")
    updated_at: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "L7PolicyItem":
        """Build an L7PolicyItem from a raw L7PolicyDto."""
        rules = data.get("l7Rules")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            action=data.get("action") or "",
            redirect_pool_id=data.get("redirectPoolId") or "",
            redirect_pool_name=data.get("redirectPoolName") or "",
            redirect_url=data.get("redirectUrl") or "",
            redirect_http_code=data.get("redirectHttpCode"),
            keep_query_string=bool(data.get("keepQueryString")),
            position=data.get("position"),
            description=data.get("description") or "",
            rules=[
                L7RuleItem.from_api(r)
                for r in (rules if isinstance(rules, list) else [])
                if isinstance(r, dict)
            ],
            display_status=data.get("displayStatus") or "",
            progress_status=data.get("progressStatus") or "",
            created_at=str(data.get("createdAt") or ""),
            updated_at=str(data.get("updatedAt") or ""),
        )


class L7PolicyListData(BaseModel):
    """Structured response for list_l7_policies."""

    load_balancer_id: str = Field(..., description="Load balancer the listener belongs to")
    listener_id: str = Field(..., description="Listener the policies belong to")
    policies: list[L7PolicyItem] = Field(
        default_factory=list,
        description="Policies in the order the API returned them; `position` is authoritative",
    )
