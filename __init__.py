# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sharp 4D Video Plugin for LichtFeld Studio.

Uses SHARP to generate 4D Gaussian Splats from video input.
"""

import lichtfeld as lf

from .panels import SharpVideoPanel

_classes = [SharpVideoPanel]


def on_load():
    """Called when plugin loads."""
    for cls in _classes:
        lf.register_class(cls)
    lf.ui.set_panel_space(SharpVideoPanel.idname, "MAIN_PANEL_TAB")
    lf.ui.set_panel_order(SharpVideoPanel.idname, 10000)
    lf.log.info("Sharp 4D Video plugin loaded")


def on_unload():
    """Called when plugin unloads."""
    for cls in reversed(_classes):
        lf.unregister_class(cls)
    lf.log.info("Sharp 4D Video plugin unloaded")


__all__ = [
    "SharpVideoPanel",
]
