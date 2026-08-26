"""Opt-in Yatima runtime-integrity plugin."""

from __future__ import annotations

from . import runtime_integrity


def register(ctx) -> None:
    """Register only lifecycle adapters when the operator enables this plugin."""

    runtime_integrity.register_plugin(ctx)


__all__ = ["register", "runtime_integrity"]
