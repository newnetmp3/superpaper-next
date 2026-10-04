"""Compatibility and isolation checks for the source module split."""

import ast
from pathlib import Path

from PIL import Image

from superpaper import image_framing, monitor_geometry
from superpaper import wallpaper_processing as processing
from superpaper.wallpaper_sources import SourceFileHandler


def test_geometry_old_import_path_is_still_exact_public_callable():
    """Existing plugins and tests can continue importing from wallpaper_processing."""
    for name in (
        "compute_canvas",
        "compute_crop_tuples",
        "compute_ppi_corrected_res_array",
        "compute_working_canvas",
        "get_all_centers",
        "translate_crops",
    ):
        assert getattr(processing, name) is getattr(monitor_geometry, name)
    assert processing.resize_to_fill is image_framing.resize_to_fill


def test_geometry_is_independent_of_gui_and_wallpaper_setter():
    directory = Path(__file__).resolve().parents[1] / "superpaper"
    for filename in ("monitor_geometry.py", "image_framing.py"):
        source = (directory / filename).read_text(encoding="utf-8")
        imports = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        imported_names = " ".join(ast.unparse(node) for node in imports)
        assert "gui" not in imported_names
        assert "wx" not in imported_names
        assert "wallpaper_processing" not in imported_names


def test_extracted_framing_returns_same_pixels_through_public_entry_point():
    with Image.new("RGB", (7, 5), "navy") as source:
        actual = processing.resize_to_fill(source, (4, 4), zoom=1.4, offset=(-0.3, 0.2))
        expected = image_framing.resize_to_fill(source, (4, 4), zoom=1.4, offset=(-0.3, 0.2))
        assert actual.size == (4, 4)
        assert actual.tobytes() == expected.tobytes()


def test_independent_slideshow_source_handler_preserves_legacy_api(profile_modules, tmp_path):
    data, _ = profile_modules
    first = tmp_path / "first image.png"
    second = tmp_path / "second.png"
    first.touch()
    second.touch()
    handler = data.ProfileData.Filehandler([[str(first)], [str(second)]], "alphabetical")
    assert isinstance(handler, SourceFileHandler)
    assert handler.next_wallpaper_files() == [str(first), str(second)]
    assert handler.next_wallpaper_files(peek=True) == [str(first), str(second)]


def test_dialog_families_live_in_dedicated_files():
    root = Path(__file__).resolve().parents[1] / "superpaper"
    owners = {
        "display_position_dialog.py": ("DisplayPositionEntry",),
        "perspective_dialog.py": ("PerspectiveConfig",),
        "settings_dialog.py": ("SettingsFrame", "SettingsPanel"),
        "help_dialog.py": ("HelpFrame", "HelpPanel", "HelpPopup"),
        "studio_widgets.py": ("StudioNavigationButton", "StudioActionButton", "StudioSegmentButton"),
    }
    for filename, classes in owners.items():
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        assert defined == set(classes)

    legacy = (root / "configuration_dialogs.py").read_text(encoding="utf-8")
    assert "from superpaper.display_position_dialog import DisplayPositionEntry" in legacy
    assert "from superpaper.perspective_dialog import PerspectiveConfig" in legacy
    assert "from superpaper.help_dialog import HelpFrame, HelpPanel, HelpPopup" in legacy


def test_gui_still_exports_existing_studio_controls():
    root = Path(__file__).resolve().parents[1] / "superpaper"
    gui = (root / "gui.py").read_text(encoding="utf-8")
    assert "from superpaper.studio_widgets import StudioActionButton, StudioNavigationButton, StudioSegmentButton" in gui
    assert "class WallpaperSettingsPanel(wx.ScrolledWindow):" in gui
    assert "from superpaper.wallpaper_preview_panel import WallpaperPreviewPanel" in gui
    preview = (root / "wallpaper_preview_panel.py").read_text(encoding="utf-8")
    assert "class WallpaperPreviewPanel(wx.Panel):" in preview
