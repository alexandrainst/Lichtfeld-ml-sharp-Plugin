# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility package entrypoint for the SHARP 4D plugin."""

from .plugin import on_load, on_unload

__all__ = ["on_load", "on_unload"]
