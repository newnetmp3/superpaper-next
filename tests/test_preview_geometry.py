"""Regression tests for early wx layout and subpixel bezel dimensions."""

import ast
from pathlib import Path

import pytest

from superpaper.preview_geometry import (
    comparison_drag_fraction,
    comparison_hit_region,
    crop_overflow,
    desktop_preview_layout,
    fit_preview_canvas,
    has_positive_area,
    pan_offset_for_drag,
    usable_preview_area,
    wheel_scroll_units,
)


@pytest.mark.parametrize("size", [(0, 400), (1080, 0), (-1, 10), (31, 500), (100, 31)])
def test_incomplete_layout_is_not_ready_for_preview(size):
    assert not usable_preview_area(size)


def test_normal_layout_is_ready_for_preview():
    assert usable_preview_area((1080, 400))
    assert usable_preview_area((32, 32))


@pytest.mark.parametrize("size", [(0, 500), (500, 0), (0, 0), (-1, 5)])
def test_subpixel_bezel_has_no_bitmap_area(size):
    assert not has_positive_area(size)


def test_nonzero_bezel_is_drawable():
    assert has_positive_area((1, 500))


@pytest.mark.parametrize("canvas", [(1920, 1080), (7680, 2160), (1, 50000)])
def test_canvas_dimensions_are_never_rounded_to_zero(canvas):
    size, _pos, scaling = fit_preview_canvas(canvas, (300, 100))
    assert all(pixel > 0 for pixel in size)
    assert scaling > 0


def test_canvas_centered_in_preview():
    size, position, _scale = fit_preview_canvas((1920, 1080), (1080, 400))
    assert size[1] == 376
    assert position[0] >= 0
    assert position[1] >= 0


@pytest.mark.parametrize(
    ("canvas", "work"),
    [((1920, 1080), (0, 100)), ((0, 1080), (300, 100)), ((1920, 0), (300, 100))],
)
def test_fit_rejects_invalid_geometry(canvas, work):
    with pytest.raises(ValueError):
        fit_preview_canvas(canvas, work)


def test_dragging_right_moves_wallpaper_right_not_crop_window():
    assert pan_offset_for_drag((0.0, 0.0), (25, 0), (100, 0)) == (-0.5, 0.0)


def test_dragging_clamps_to_edges_without_blank_space():
    assert pan_offset_for_drag((0.0, 0.0), (999, -999), (200, 200)) == (-1.0, 1.0)


def test_no_overflow_keeps_original_axis():
    result = pan_offset_for_drag((0.3, 0.2), (30, 90), (120, 0))
    assert result[0] == pytest.approx(-0.2)
    assert result[1] == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("image", "target", "zoom", "expected"),
    [
        ((1920, 1080), (960, 540), 1.0, (0, 0)),
        ((1920, 1080), (960, 540), 2.0, (960, 540)),
        ((1920, 1080), (600, 600), 1.0, (467, 0)),
    ],
)
def test_overflow_matches_cover_crop(image, target, zoom, expected):
    assert crop_overflow(image, target, zoom) == expected


def test_wheel_scrolls_entire_window_for_notches():
    assert wheel_scroll_units(-120, 120, 3) == (3, 0.0)
    assert wheel_scroll_units(120, 120, 3) == (-3, 0.0)


def test_wheel_retains_partial_steps():
    first = wheel_scroll_units(-15, 120, 3)
    second = wheel_scroll_units(-30, 120, 3, first[1])
    assert first == (0, 0.375)
    assert second == (1, 0.125)


def test_settings_panel_does_not_scroll_due_to_focus_or_mouse_exit():
    """Guard the wx.ScrolledWindow overrides without importing wx in CI."""
    source = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")

    for method_name in ("ShouldScrollToChildOnFocus", "SendAutoScrollEvents"):
        method = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
        result = method.body[-1]
        assert isinstance(result, ast.Return)
        assert isinstance(result.value, ast.Constant)
        assert result.value.value is False


def test_no_wheel_rotation_does_not_move_the_scroll_position():
    """Entering a window without wheel movement must not trigger scrolling."""
    assert wheel_scroll_units(0, 120, 3) == (0, 0.0)


def test_preview_uses_nearly_all_available_stage_width_without_stretching():
    canvas = (7000, 1440)
    stage = (1050, 265)
    (width, height), position, scale = fit_preview_canvas(canvas, stage)
    assert width >= 0.93 * stage[0]
    assert width <= stage[0]
    assert height <= stage[1]
    assert abs(width / height - canvas[0] / canvas[1]) < 0.03
    assert position[0] >= 0 and position[1] >= 0
    assert scale > 0


def test_bitmap_children_receive_wallpaper_drag_events():
    """Displayed wx.StaticBitmaps eat mouse events unless bound directly."""
    source = Path(__file__).resolve().parents[1] / "superpaper" / "wallpaper_preview_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    preview = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel"
    )

    def method(name):
        function = next(node for node in preview.body if isinstance(node, ast.FunctionDef) and node.name == name)
        return ast.unparse(function)

    constructor = method("__init__")
    assert "self.bind_background_drag()" in constructor
    assert "self.bind_wallpaper_bitmap_drag()" in constructor

    child_bindings = method("bind_wallpaper_bitmap_drag")
    assert "self.st_bmp_canvas" in child_bindings
    assert "self.preview_img_list" in child_bindings
    for event_type in ("wx.EVT_LEFT_DOWN", "wx.EVT_LEFT_UP", "wx.EVT_MOTION"):
        assert f"bitmap.Bind({event_type}," in child_bindings

    down = method("_on_background_down")
    motion = method("_on_background_motion")
    assert "self._preview_mouse_position(event)" in down
    assert "self._preview_mouse_position(event)" in motion
    assert "self.CaptureMouse()" in down
    assert "pan_offset_for_drag(" in motion


def test_wallpaper_bitmap_drag_coordinates_are_relative_to_parent():
    """Simulate child-local and parent mouse events without wxPython."""
    source = Path(__file__).resolve().parents[1] / "superpaper" / "wallpaper_preview_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    preview = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel"
    )
    method = next(
        node for node in preview.body if isinstance(node, ast.FunctionDef) and node.name == "_preview_mouse_position"
    )
    namespace = {}
    code = compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<preview-pointer>", "exec")
    exec(code, namespace)
    translate = namespace["_preview_mouse_position"]

    class Point:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class Bitmap:
        def ClientToScreen(self, point):
            return Point(point.x + 220, point.y + 160)

    class Panel:
        def ScreenToClient(self, point):
            return Point(point.x - 180, point.y - 120)

    class Event:
        def __init__(self, source, point):
            self.source = source
            self.point = point

        def GetEventObject(self):
            return self.source

        def GetPosition(self):
            return self.point

    panel = Panel()
    child = Bitmap()
    original = Point(15, 25)
    mapped = translate(panel, Event(child, original))
    assert (mapped.x, mapped.y) == (55, 65)

    parent_mapped = translate(panel, Event(panel, original))
    assert parent_mapped is original


def test_desktop_preview_uses_digital_offsets_and_actual_monitor_dimensions():
    # Representative screenshot setup: three different resolutions and
    # different monitor top edges in the virtual desktop.
    displays = [
        ((2259, 1271), (0, 140)),
        ((2560, 1440), (2259, 272)),
        ((1920, 1080), (4819, 222)),
    ]
    canvas_size, position, rects = desktop_preview_layout(displays, (1080, 265))
    assert canvas_size[0] <= 1080
    assert canvas_size[1] <= 265
    assert rects[0][0] < rects[1][0] < rects[2][0]
    assert rects[0][1] < rects[2][1] < rects[1][1]
    # Center monitor must be relatively taller; no forced equal-height tiles.
    assert rects[1][3] > rects[0][3] > rects[2][3]
    assert rects[0][0] >= position[0]
    assert rects[0][1] >= position[1]


def test_desktop_preview_handles_negative_offsets_and_empty_inputs():
    _canvas, _position, rects = desktop_preview_layout(
        [((1920, 1080), (-1920, -200)), ((2560, 1440), (0, 0))], (720, 400)
    )
    assert rects[0][0] < rects[1][0]
    assert rects[0][1] < rects[1][1]
    with pytest.raises(ValueError):
        desktop_preview_layout([], (800, 300))
    with pytest.raises(ValueError):
        desktop_preview_layout([((0, 1080), (0, 0))], (800, 300))


def test_desktop_preview_preserves_mouse_drags_and_calibration_mode():
    source = Path(__file__).resolve().parents[1] / "superpaper" / "wallpaper_preview_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    preview = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel"
    )
    methods = {node.name: ast.unparse(node) for node in preview.body if isinstance(node, ast.FunctionDef)}
    assert "self.desktop_preview.Bind(wx.EVT_LEFT_DOWN, self._on_background_down)" in methods["__init__"]
    assert "self.desktop_preview.IsShown()" in methods["_drag_target"]
    assert "self.set_desktop_layout(False)" in methods["onConfigure"]
    assert "desktop_preview_layout(displays, self.GetClientSize())" in methods["_desktop_preview_rectangles"]
    assert "prepare_cloud_upscaled_image(" not in methods["_update_desktop_preview"]


def test_handle_hit_detection_tracks_staggered_desktop_crop_mapping():
    # Physical compositor cropped this monitor out of a wider source canvas,
    # then desktop preview scaled it down for the actual digital arrangement.
    region = (400, 75, 240, 160, 300, 600, 1200)
    # 50% across the full canvas is x=600; inside this crop = x=520.
    assert comparison_hit_region([region], (520, 130), 0.5) == pytest.approx(480)
    assert comparison_hit_region([region], (490, 130), 0.5) is None
    assert comparison_hit_region([region], (520, 60), 0.5) is None
    # At 90% the divider is outside the selected monitor, so no handle exists.
    assert comparison_hit_region([region], (620, 130), 0.9) is None


def test_dragging_handle_maps_screen_distance_to_fraction_and_clamps():
    assert comparison_drag_fraction(0.5, 120, 480) == pytest.approx(0.75)
    assert comparison_drag_fraction(0.5, -120, 480) == pytest.approx(0.25)
    assert comparison_drag_fraction(0.5, 2000, 480) == 1.0
    assert comparison_drag_fraction(0.5, -2000, 480) == 0.0
    assert comparison_drag_fraction(0.5, 200, 0) == 0.5


def test_handle_hit_skips_gaps_and_invalid_screen_regions():
    regions = [
        (10, 10, 200, 110, 0, 200, 400),
        (340, 40, 200, 120, 200, 200, 400),
        (0, 0, 0, 200, 0, 200, 400),
    ]
    # 50% boundary is at the left edge of second monitor.
    assert comparison_hit_region(regions, (340, 70), 0.5) == 400
    assert comparison_hit_region(regions, (275, 70), 0.5) is None
