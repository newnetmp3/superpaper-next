"""Compatibility imports for Superpaper's configuration dialogs.

Dialogs are organized by responsibility in dedicated modules. This module
retains the pre-refactor class import paths for external callers and the GUI.
"""

from superpaper.display_position_dialog import DisplayPositionEntry
from superpaper.help_dialog import HelpFrame, HelpPanel, HelpPopup
from superpaper.perspective_dialog import PerspectiveConfig
from superpaper.settings_dialog import SettingsFrame, SettingsPanel

__all__ = [
    "DisplayPositionEntry",
    "HelpFrame",
    "HelpPanel",
    "HelpPopup",
    "PerspectiveConfig",
    "SettingsFrame",
    "SettingsPanel",
]
