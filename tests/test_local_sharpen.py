"""Sharpening is available locally without cloud uploads or model weights."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from superpaper import cloud_upscale, image_adjustments


def edge_image(size=(80, 60)):
    image = Image.new("RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            v = 30 if x < size[0] // 2 else 220
            image.putpixel((x, y), (v, v, v))
    return image


def test_disabled_cloud_sharpen_works_without_upload(tmp_path, monkeypatch):
    image = edge_image()
    calls = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: calls.append(args))
    result = cloud_upscale.prepare_cloud_upscaled_image(
        image,
        tmp_path / "nonexistent.png",
        (200, 150),
        zoom=1.0,
        enabled=False,
        cache_root=tmp_path,
        sharpen=100,
    )
    assert result is not image
    assert result.tobytes() != image.tobytes()
    assert calls == []


def test_disabled_cloud_zero_sharpen_returns_original(tmp_path):
    image = edge_image()
    result = cloud_upscale.prepare_cloud_upscaled_image(
        image,
        tmp_path / "nonexistent.png",
        (200, 150),
        zoom=1.0,
        enabled=False,
        cache_root=tmp_path,
        sharpen=0,
    )
    assert result is image


def test_enabled_but_not_needed_still_sharpens_locally(tmp_path, monkeypatch):
    image = edge_image()
    calls = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: calls.append(args))
    result = cloud_upscale.prepare_cloud_upscaled_image(
        image,
        tmp_path / "nonexistent.png",
        (40, 30),
        zoom=1.0,
        enabled=True,
        cache_root=tmp_path,
        sharpen=75,
    )
    assert result.tobytes() != image.tobytes()
    assert calls == []


def test_enabled_cloud_failure_falls_back_to_sharpened_original(tmp_path, monkeypatch):
    image = edge_image()
    source = tmp_path / "source.png"
    image.save(source)
    calls = []

    def offline(*args):
        calls.append(True)
        raise ConnectionError

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", offline)
    result = cloud_upscale.prepare_cloud_upscaled_image(
        image,
        source,
        (160, 120),
        zoom=1.0,
        enabled=True,
        cache_root=tmp_path,
        sharpen=100,
    )
    assert result.tobytes() != image.tobytes()
    assert calls == [True]


def test_preview_sharpens_original_not_cloud_source():
    gui = Path(__file__).resolve().parents[1] / "superpaper" / "wallpaper_preview_panel.py"
    tree = ast.parse(gui.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel")
    render = [node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "resize_and_bitmap"][-1]
    source = ast.unparse(render)
    assert "locally_sharpen(oriented, self.sharpen)" in source
    assert "prepare_cloud_upscaled_image" not in source


def test_gui_enables_local_sharpen_without_cloud():
    gui = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(gui.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")
    method = next(
        node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "_sync_cloud_quality_controls"
    )
    source = ast.unparse(method)
    assert "self.ch_cloud_scale.Enable(cloud_enabled)" in source
    assert "self.sld_cloud_sharpen.Enable(self.sld_zoom.IsEnabled())" in source
    assert "self.cb_cloud_upscale.GetValue() and self.cb_cloud_upscale.IsEnabled()" in source


def test_real_wallpaper_pipeline_sharpens_when_cloud_off(profile_modules, monkeypatch, tmp_path):
    _, wpproc = profile_modules
    cache = tmp_path / "render-cache"
    cache.mkdir()
    source = tmp_path / "image.png"
    edge_image().save(source)
    monkeypatch.setattr(wpproc, "TEMP_PATH", str(cache))
    monkeypatch.setattr(wpproc, "RESOLUTION_ARRAY", [(80, 60)])
    monkeypatch.setattr(wpproc, "DISPLAY_OFFSET_ARRAY", [(0, 0)])
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "sharpen")
    monkeypatch.setattr(wpproc, "set_wallpaper", lambda *args: None)
    calls = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: calls.append(args))
    profile = SimpleNamespace(
        name="sharpen",
        zoom=1.0,
        offsets=(0.0, 0.0),
        cloud_upscale=False,
        cloud_upscale_scale="auto",
        cloud_upscale_sharpen=0,
        next_wallpaper_files=lambda: [str(source)],
    )
    assert wpproc.span_single_image_simple(profile, force=True) == 0
    output = cache / "sharpen-a.png"
    with Image.open(output) as unchanged:
        first = unchanged.convert("RGB").tobytes()
    profile.cloud_upscale_sharpen = 100
    assert wpproc.span_single_image_simple(profile, force=True) == 0
    output = cache / "sharpen-b.png"
    with Image.open(output) as sharper:
        second = sharper.convert("RGB").tobytes()
    assert first != second
    assert calls == []


def test_local_split_is_visible_even_when_adjustments_are_neutral():
    image = Image.new("RGB", (640, 180), (85, 92, 101))
    unchanged = image.tobytes()
    result = image_adjustments.split_local_preview(image, image, 0.5, has_adjustments=False)
    assert result.size == image.size
    assert result.getpixel((320, 90)) == (62, 172, 255)
    assert result.getpixel((100, 90)) == image.getpixel((100, 90))
    assert result.getpixel((550, 90)) == image.getpixel((550, 90))
    assert result.tobytes() != unchanged
    assert image.tobytes() == unchanged


def test_moving_split_moves_real_original_vs_adjusted_pixel_boundary():
    original = Image.new("RGB", (640, 180), (255, 0, 0))
    adjusted = Image.new("RGB", (640, 180), (0, 255, 0))
    quarter = image_adjustments.split_local_preview(original, adjusted, 0.25, has_adjustments=True)
    three_quarters = image_adjustments.split_local_preview(original, adjusted, 0.75, has_adjustments=True)
    assert quarter.getpixel((100, 90)) == (255, 0, 0)
    assert quarter.getpixel((300, 90)) == (0, 255, 0)
    assert quarter.getpixel((160, 90)) == (62, 172, 255)
    assert three_quarters.getpixel((300, 90)) == (255, 0, 0)
    assert three_quarters.getpixel((530, 90)) == (0, 255, 0)
    assert three_quarters.getpixel((480, 90)) == (62, 172, 255)
    assert original.getpixel((300, 90)) == (255, 0, 0)
    assert adjusted.getpixel((300, 90)) == (0, 255, 0)


@pytest.mark.parametrize("fraction,boundary", [(-0.5, 0), (1.5, 639)])
def test_compare_fraction_is_safely_clamped(fraction, boundary):
    image = Image.new("RGB", (640, 180))
    out = image_adjustments.split_local_preview(image, image, fraction)
    assert out.getpixel((boundary, 90)) == (62, 172, 255)


def test_invalid_comparison_geometry_is_rejected():
    with pytest.raises(ValueError):
        image_adjustments.split_local_preview(
            Image.new("RGB", (20, 20)),
            Image.new("RGB", (19, 20)),
            0.5,
        )


def test_preview_comparison_remains_local_even_when_cloud_is_enabled():
    gui = Path(__file__).resolve().parents[1] / "superpaper" / "wallpaper_preview_panel.py"
    tree = ast.parse(gui.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperPreviewPanel")
    method = [node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "resize_and_bitmap"][-1]
    implementation = ast.unparse(method)
    assert "if self.compare_original:" in implementation
    assert "split_local_preview(" in implementation
    assert "has_adjustments=has_adjustments" in implementation
    assert "prepare_cloud_upscaled_image" not in implementation
    assert "apply_image_shader" not in implementation
