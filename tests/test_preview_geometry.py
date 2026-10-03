"""Regression tests for early wx layout and subpixel bezel dimensions."""

import pytest

from superpaper.preview_geometry import fit_preview_canvas, has_positive_area, usable_preview_area


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
    assert size[1] == 360
    assert position[0] >= 0
    assert position[1] >= 0


@pytest.mark.parametrize(
    ("canvas", "work"),
    [((1920, 1080), (0, 100)), ((0, 1080), (300, 100)), ((1920, 0), (300, 100))],
)
def test_fit_rejects_invalid_geometry(canvas, work):
    with pytest.raises(ValueError):
        fit_preview_canvas(canvas, work)
