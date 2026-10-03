"""Source-relative framing stays identical before and after AI enhancement."""

import ast
from pathlib import Path

import pytest
from PIL import Image

from superpaper.preview_geometry import original_frame_box


@pytest.mark.parametrize(
    ("zoom", "offset"),
    [
        (1.0, (0.0, 0.0)),
        (1.25, (-0.75, 0.5)),
        (2.0, (1.0, -1.0)),
        (3.0, (-0.25, -0.25)),
    ],
)
def test_original_framing_is_independent_of_render_resolution(zoom, offset):
    original = (160, 90)
    small = original_frame_box(original, (240, 135), zoom, offset)
    large = original_frame_box(original, (3840, 2160), zoom, offset)
    assert small == pytest.approx(large)


def test_source_relative_offsets_always_preserve_visible_region():
    original = (100, 60)
    assert original_frame_box(original, (50, 50), 1.0, (1.0, -1.0)) == pytest.approx((40, 0, 100, 60))
    assert original_frame_box(original, (100, 100), 2.0, (-1.0, 1.0)) == pytest.approx((0, 30, 30, 60))


@pytest.mark.parametrize(
    ("zoom", "offset"),
    [
        (1.0, (0.0, 0.0)),
        (1.35, (-0.67, 0.31)),
        (2.0, (0.8, -0.5)),
        (3.25, (-1.0, 1.0)),
    ],
)
def test_enhanced_wallpaper_keeps_original_feature_positions(profile_modules, zoom, offset):
    _, wpproc = profile_modules
    original = Image.new("RGB", (160, 100))
    for y in range(100):
        for x in range(160):
            original.putpixel((x, y), (int(x * 255 / 159), int(y * 255 / 99), 40))
    enhanced = original.resize((640, 400), Image.Resampling.NEAREST)
    target = (200, 110)
    raw = wpproc.resize_to_fill(original, target, zoom=zoom, offset=offset)
    applied = wpproc.resize_to_fill(enhanced, target, zoom=zoom, offset=offset, reference_size=original.size)
    # Normalized placement should agree within the filtering/rounding error.
    for y in (12, 35, 60, 95):
        for x in (13, 50, 100, 175):
            before = raw.getpixel((x, y))
            after = applied.getpixel((x, y))
            assert abs(before[0] - after[0]) <= 5
            assert abs(before[1] - after[1]) <= 5


def test_profile_persists_original_selection_zoom_pan_and_cloud_flag(profile_modules, tmp_path):
    data, _wpproc = profile_modules
    image_path = tmp_path / "source.png"
    Image.new("RGB", (90, 60), "purple").save(image_path)
    temp = data.TempProfileData()
    temp.name = "enhanced"
    temp.spanmode = "single"
    temp.slideshow = False
    temp.cloud_upscale = True
    temp.zoom = 2.15
    temp.align = (-0.42, 0.73)
    temp.selected = [str(image_path)]
    temp.paths_array = [str(image_path)]
    saved = tmp_path / "enhanced.profile"
    temp.save(filename=saved)

    profile = data.parse_profile_file(saved)
    assert profile.cloud_upscale is True
    assert profile.zoom == pytest.approx(2.15)
    assert profile.offsets == pytest.approx((-0.42, 0.73))
    assert profile.selected == [str(image_path)]
    assert profile.next_wallpaper_files(peek=True) == [str(image_path)]


def test_preview_uses_original_orientation_without_cloud_api_calls():
    source = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel")
    # The first two declarations are @overload stubs; inspect the body.
    render = [
        node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "resize_and_bitmap"
    ][-1]
    text = ast.unparse(render)
    assert "ImageOps.exif_transpose(source)" in text
    assert "prepare_cloud_upscaled_image" not in text


def test_save_apply_persists_before_rendering():
    source = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")
    method = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "onSaveAndApply")
    text = ast.unparse(method)
    assert "self.onSave(None)" in text
    assert "self.onApply(event)" in text
    assert text.index("self.onSave(None)") < text.index("self.onApply(event)")


def test_cloud_provider_rejects_changed_image_aspect_ratio(tmp_path):
    from superpaper.cloud_upscale import _usable_image

    bad = tmp_path / "cropped.png"
    Image.new("RGB", (240, 240)).save(bad)
    assert _usable_image(bad, (120, 60)) is None

    good = tmp_path / "enhanced.png"
    Image.new("RGB", (240, 120)).save(good)
    assert _usable_image(good, (120, 60)).size == (240, 120)
