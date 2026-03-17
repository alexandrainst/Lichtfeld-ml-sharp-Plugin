# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""RmlUI key constants with safe fallbacks."""

from __future__ import annotations

import lichtfeld as lf


def _resolve_escape_key() -> int:
    key_module = getattr(lf, "key", None)
    if key_module is not None and hasattr(key_module, "ESCAPE"):
        return int(key_module.ESCAPE)

    ui_module = getattr(lf, "ui", None)
    ui_key_module = getattr(ui_module, "key", None) if ui_module is not None else None
    if ui_key_module is not None and hasattr(ui_key_module, "ESCAPE"):
        return int(ui_key_module.ESCAPE)

    return 256


KI_ESCAPE = _resolve_escape_key()
