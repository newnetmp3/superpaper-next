"""Non-GUI checks for mockup-aligned Wallpaper Studio layout and live controls.

wxPython is unavailable on headless runners; inspect the control wiring while
unit testing the image controls through the existing rendering test suite.
"""

import ast
from pathlib import Path


GUI = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"


def _method(class_name, method):
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return ast.unparse(
        next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == method)
    )


def test_preview_stage_is_compact_and_not_stretched_to_window_height():
    setup = _method("WallpaperSettingsPanel", "__init__")
    assert "self.studio_canvas_column.Add(self.sizer_top_half, 0," in setup
    assert "self.wpprev_pnl.SetMaxSize(wx.Size(10000, 365))" in setup
    preview = _method("WallpaperPreviewPanel", "__init__")
    assert "self.preview_size = (1080, 315)" in preview


def test_inactive_advanced_controls_are_hidden_recursively():
    display = _method("WallpaperSettingsPanel", "show_adv_setting_sizer")
    assert "recursive=True" in display
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert "self.studio_inspector.Show(section, show=key == name, recursive=True)" in switching
    assert "self.sizer_displays.Show(" in switching
    assert "name == 'Displays'" in switching
    assert "self.studio_canvas_column.Show(self.sizer_display_editor" not in switching


def test_only_one_apply_button_in_persistent_footer():
    footer = _method("WallpaperSettingsPanel", "create_sizer_bottom_buttons")
    assert "self.sizer_bottom_buttonrow.Add(self.button_save_apply" in footer
    assert "self.sizer_bottom_buttonrow.Add(self.button_apply" not in footer
    header = _method("WallpaperSettingsPanel", "create_studio_navigation")
    assert "self.studio_apply.Bind(wx.EVT_BUTTON, self.onApply)" in header


def test_navigation_and_real_monitor_layout_editor():
    header = _method("WallpaperSettingsPanel", "create_studio_navigation")
    assert "Arrange monitors in preview..." in header
    assert "self._studio_arrange_displays" in header
    arrange = _method("WallpaperSettingsPanel", "_studio_arrange_displays")
    assert "self.wpprev_pnl.onConfigure(None)" in arrange
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert "self.studio_canvas_column.Show(self.studio_display_editor" in switching


def test_preview_toolbar_zoom_and_alignment_are_real():
    init = _method("WallpaperSettingsPanel", "create_studio_navigation")
    for label in ("Left", "Center", "Right"):
        assert f'("{label}",' in init
    align = _method("WallpaperSettingsPanel", "_studio_align_image")
    assert "self.sld_offx.SetValue(value)" in align
    assert "self.onZoomOffsetChange(None)" in align
    zoom = _method("WallpaperSettingsPanel", "_studio_quick_zoom_changed")
    assert "self.sld_zoom.SetValue(self.studio_quick_zoom.GetValue())" in zoom
    refresh = _method("WallpaperSettingsPanel", "onZoomOffsetChange")
    assert "self.studio_quick_zoom.SetValue(zoom_pct)" in refresh


def test_compare_slider_changes_original_vs_adjusted_boundary():
    toggle = _method("WallpaperSettingsPanel", "_studio_toggle_compare")
    assert "self.studio_split_slider.Enable(self.studio_compare.GetValue())" in toggle
    adjust = _method("WallpaperSettingsPanel", "_studio_compare_position")
    assert "self.wpprev_pnl.compare_fraction" in adjust
    panel = _method("WallpaperPreviewPanel", "resize_and_bitmap")
    assert "self.compare_fraction" in panel
    assert "plain.crop((0, 0, divider, pil.height))" in panel


def test_processing_tabs_use_existing_cloud_shader_and_local_controls():
    switching = _method("WallpaperSettingsPanel", "_set_processing_tab")
    assert "self.sizer_setting_processing.Show(self.sizer_processing_cloud" in switching
    assert "self.sizer_setting_processing.Show(self.sizer_processing_shaders" in switching
    assert "self.sizer_setting_processing.Show(self.sizer_processing_local" in switching
    assert "recursive=True" in switching
    constructor = _method("WallpaperSettingsPanel", "create_sizer_settings_left")
    assert "self.sizer_processing_local.Add(sharpen_grid" in constructor
    assert "self.sizer_processing_cloud.Add(cloud_grid" in constructor


def test_profile_gallery_supports_larger_cards_and_filtering():
    create = _method("WallpaperSettingsPanel", "create_studio_gallery")
    assert "wx.SearchCtrl" in create
    assert "self._refresh_profile_gallery" in create
    refresh = _method("WallpaperSettingsPanel", "_refresh_profile_gallery")
    assert "width, height = (234, 134)" in refresh
    assert "filter_text" in refresh
    assert "profile.name.lower()" in refresh
