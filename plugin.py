"""Plugin entrypoint for the SHARP 4D plugin."""

import lichtfeld as lf

from .panels import SharpVideoPanel

_CLASSES = [SharpVideoPanel]


def on_load():
    """Register plugin classes."""
    for cls in _CLASSES:
        lf.register_class(cls)
    lf.ui.set_panel_space(SharpVideoPanel.id, lf.ui.PanelSpace.MAIN_PANEL_TAB)
    lf.ui.set_panel_order(SharpVideoPanel.id, SharpVideoPanel.order)
    lf.log.info("Sharp 4D Video plugin loaded")


def on_unload():
    """Unregister plugin classes."""
    for cls in reversed(_CLASSES):
        lf.unregister_class(cls)
    lf.log.info("Sharp 4D Video plugin unloaded")
