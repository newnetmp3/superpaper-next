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
    matches = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == method]
    return ast.unparse(matches[-1])


def test_preview_stage_is_compact_and_not_stretched_to_window_height():
    setup = _method("WallpaperSettingsPanel", "__init__")
    assert "self.studio_canvas_column.Add(self.sizer_top_half, 0," in setup
    assert "self.wpprev_pnl.SetMaxSize(wx.Size(10000, 295))" in setup
    preview = _method("WallpaperPreviewPanel", "__init__")
    assert "self.preview_size = (1080, 265)" in preview


def test_inactive_advanced_controls_are_hidden_recursively():
    display = _method("WallpaperSettingsPanel", "show_adv_setting_sizer")
    assert "recursive=True" in display
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert "self.studio_inspector.Show(section, show=key == name, recursive=True)" in switching
    assert "self.sizer_displays.Show(" in switching
    assert "name == 'Displays'" in switching
    assert "self.studio_canvas_column.Show(self.sizer_display_editor" not in switching


def test_only_one_apply_button_in_persistent_footer():
    footer = _method("WallpaperSettingsPanel", "create_sizer_bottom_buttonrow")
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
        assert f"'{label}'" in init
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


def test_profile_gallery_supports_clickable_cards_and_filtering():
    create = _method("WallpaperSettingsPanel", "create_studio_gallery")
    assert "wx.SearchCtrl" in create
    assert "self.studio_gallery_cards = wx.WrapSizer(wx.HORIZONTAL, flags=0)" in create
    assert "self.studio_quick_profile_row = wx.WrapSizer(wx.HORIZONTAL, flags=0)" in create
    refresh = _method("WallpaperSettingsPanel", "_refresh_profile_gallery")
    assert "self._studio_make_profile_card(self.studio_gallery_scroller, profile, 234, 134)" in refresh
    assert "filter_text" in refresh
    assert "profile.name.lower()" in refresh
    card = _method("WallpaperSettingsPanel", "_studio_make_profile_card")
    assert "self._studio_profile_thumbnail(profile, width, height)" in card
    assert "self._studio_select_profile(profile_name)" in card


def test_sidebar_navigation_uses_custom_painted_controls_and_real_workspace_callbacks():
    sidebar = _method("WallpaperSettingsPanel", "create_studio_navigation")
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert "StudioNavigationButton(self.studio_sidebar, name, icon)" in sidebar
    assert "self._set_studio_workspace(workspace)" in sidebar
    assert "button.SetSelected(key == name)" in switching
    painting = _method("StudioNavigationButton", "_paint")
    assert "wx.AutoBufferedPaintDC(self)" in painting
    assert "DrawRoundedRectangle" in painting


def test_wallpaper_uses_preview_and_live_profile_strip_without_duplicate_form_row():
    constructor = _method("WallpaperSettingsPanel", "__init__")
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert "self.studio_canvas_column.Add(self.studio_quick_profiles" in constructor
    assert "self.studio_canvas_column.Show(self.studio_quick_profiles" in switching
    assert "self.sizer_bottom_half.Show(self.sizer_profiles" in switching
    assert "name == 'Profiles'" in switching

def test_profile_tiles_keep_text_visible_and_do_not_stretch_to_row_width():
    create = _method("WallpaperSettingsPanel", "create_studio_gallery")
    assert "wx.WrapSizer(wx.HORIZONTAL, flags=0)" in create
    card = _method("WallpaperSettingsPanel", "_studio_make_profile_card")
    assert "card.SetMinSize(wx.Size(width + 18, height + 85))" in card
    assert "card_sizer.Add(detail" in card


def test_wallpaper_layout_prioritizes_full_width_preview_and_inspector():
    init = _method("WallpaperSettingsPanel", "__init__")
    assert "self.studio_inspector.SetMinSize(wx.Size(294, -1))" in init
    assert "self.wpprev_pnl.SetMinSize(wx.Size(450, 265))" in init
    nav = _method("WallpaperSettingsPanel", "create_studio_navigation")
    assert "self.studio_sidebar = wx.Panel(self, size=wx.Size(162, -1))" in nav
    switching = _method("WallpaperSettingsPanel", "_set_studio_workspace")
    assert 'self.studio_inspector.Show(self.studio_workspace_title, show=name != "Wallpapers")' in switching
