"""Opt-in Yatima K3 pre_llm_call context plugin."""

from __future__ import annotations

from .adapter import register_plugin


def register(ctx) -> None:
    """Register only when explicit namespaced settings select this plugin."""

    register_plugin(ctx)
