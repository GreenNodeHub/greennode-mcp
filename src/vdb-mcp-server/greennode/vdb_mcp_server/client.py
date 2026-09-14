"""HTTP client for the vDB API (built on greennode.mcp_core)."""

from __future__ import annotations

import json as _json
from greennode.mcp_core.auth import TokenManager
from greennode.mcp_core.http import BaseClient
from greennode.vdb_mcp_server.config import VDB_RELATIONAL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.useragent import USER_AGENT
from typing import Any, Literal


UserType = Literal["ROOT_USER", "IAM_USER"]
"""Which billing flow an order endpoint should use.

``IAM_USER`` is the **Auto Payment** flow: the order is settled immediately and
the resource gets provisioned. ``ROOT_USER`` is the manual **Checkout** flow —
the API still answers 200 with an order URL, but nothing is created until a
human completes that order in the payment console, so a caller that assumes
success ends up waiting for a resource that will never appear.

That asymmetry is why :data:`DEFAULT_USER_TYPE` is ``IAM_USER`` even though
``ROOT_USER`` is the API's own default. The header applies to the 19 endpoints
that create or resize a billable resource; everywhere else it is meaningless
and is not sent.
"""

DEFAULT_USER_TYPE: UserType = "IAM_USER"
"""Auto Payment. Every billable vDB operation should use this flow."""

USER_TYPE_HEADER = "user-type"


class VdbClient(BaseClient):
    """Async client for the vDB API (retry + token refresh from BaseClient).

    vDB is four APIs behind one gateway and one IAM token. They share nothing
    else -- not paths, not methods for the same operation, not response
    envelopes -- so every call names the family it targets rather than
    inheriting one. ``call`` is the single entry point; the inherited
    ``get``/``post``/... helpers would silently use the relational service.

    There is no project id to inject and no region to route on: the gateway
    resolves the project from the token and serves every region.
    """

    def __init__(self, config: VdbConfig, token_manager: TokenManager) -> None:
        super().__init__(
            config,
            token_manager,
            default_service=VDB_RELATIONAL_SERVICE,
            user_agent=USER_AGENT,
        )

    async def call(
        self,
        method: str,
        path: str,
        *,
        service: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
        user_type: UserType | None = None,
    ) -> Any:
        """Send a request to one vDB family.

        Args:
            method: HTTP method. ``DELETE`` may carry ``json`` -- two vDB
                delete endpoints take an array of ids in the body.
            path: Path **without** the family prefix, which lives in the base
                URL (``/v1/database-instances``, not
                ``/vdb-relational/v1/database-instances``).
            service: One of the ``VDB_*_SERVICE`` constants from ``config``.
            params: Query parameters. A list value repeats the key, which is
                how vDB expects a multi-valued filter (``status=A&status=B``).
            json: Request body.
            user_type: Billing flow for order endpoints -- see
                :data:`DEFAULT_USER_TYPE`. Omitted entirely when ``None``, so a
                non-billable call never opts into a flow by accident.
        """
        headers = {USER_TYPE_HEADER: user_type} if user_type else None
        return await self._request(
            method,
            path,
            params=params,
            json=json,
            service=service,
            headers=headers,
        )

    async def call_bytes(
        self,
        method: str,
        path: str,
        *,
        service: str,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Send a request and return the raw body, undecoded.

        One vDB endpoint answers with a file rather than JSON: a Kafka user's
        ``authen-creds`` is a ZIP archive served as ``application/octet-stream``
        -- despite the OpenAPI document describing it as an array of strings.
        Putting it through :meth:`call` raises ``UnicodeDecodeError`` on the
        first non-UTF-8 byte, which is how the real shape was found.
        """
        return await self._request(
            method,
            path,
            params=params,
            service=service,
            binary_response=True,
        )

    async def call_scalar(
        self,
        method: str,
        path: str,
        *,
        service: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send a request whose successful response is a bare scalar, not an object.

        Most Kafka writes answer with a single word rather than a document. The
        OpenAPI schema says only ``type: string`` and the content type is the
        springdoc placeholder ``*/*``, so the document does not say whether the
        body is JSON (``"success"``) or plain text (``success``) -- and the two
        parse differently.

        Rather than pick one, this reads the body as text and parses it as JSON
        only if it is JSON. Both shapes therefore work, and a future platform
        change between them cannot break a write tool. An empty body comes back
        as ``None``, as it does through :meth:`call`.
        """
        text = await self._request(
            method,
            path,
            params=params,
            json=json,
            service=service,
            raw_response=True,
        )
        if text is None or not str(text).strip():
            return None
        try:
            return _json.loads(text)
        except ValueError:
            # Plain text, which the schema permits just as much as JSON does.
            return str(text).strip()
