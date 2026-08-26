"""Shared MCP ToolAnnotations for tool registration.

Hints let clients auto-approve read-only calls and warn before destructive
ones. Pick by effect, not by API verb: a dry-run delete is READ; an
irreversible change is DESTRUCTIVE.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
