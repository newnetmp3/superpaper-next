"""Regression tests for early wx layout and subpixel bezel dimensions."""

import ast
from pathlib import Path

import pytest

from superpaper.preview_geometry import (
    crop_overflow,
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
