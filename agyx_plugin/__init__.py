"""hermes-agyx — Hermes-native AGY (Antigravity/Code Assist) tool.

Install as a Hermes plugin: drop this directory into
~/.hermes/plugins/agyx_plugin/ (or any plugins/ search path) and the tool
`agyx` is auto-discovered and registered into the `agy` toolset on startup.

The tool itself (`agyx_tool.py`) is a SINGLE SOURCE OF TRUTH: it contains all
logic and exposes `agyx_run(...)` plus the schema. No core Hermes files are
modified — this plugin only calls `ctx.register_tool(...)`.

Auth model
----------
All capabilities route through the `agy` CLI using the user's paid OAuth login
(no Gemini API key needed). A key-only fallback to the public Gemini REST API
is used only when `agy` is absent AND a GEMINI_API_KEY is set.

This plugin is intentionally standalone: it must NOT edit core Hermes files,
per Hermes plugin policy.
"""

from __future__ import annotations

from agyx_plugin.agyx_tool import (
    AGYX_SCHEMA,
    agyx_run,
    check_agyx_requirements,
)
from agyx_plugin.agyx_tool import _handle_agyx  # re-exports the handler


def register(ctx) -> None:
    """Register the agyx tool into the `agy` toolset.

    Called once by the Hermes plugin loader when this plugin is discovered.
    The tool is service-gated (check_fn) so it has zero schema footprint until
    `agy` is on PATH (or a GEMINI_API_KEY is set).
    """
    ctx.register_tool(
        name="agyx",
        toolset="agy",
        schema=AGYX_SCHEMA,
        handler=_handle_agyx,
        check_fn=check_agyx_requirements,
        is_async=False,
        emoji="🤖",
    )
