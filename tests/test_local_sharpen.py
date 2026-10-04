"""Sharpening is available locally without cloud uploads or model weights."""

import ast
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from superpaper import cloud_upscale


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
    gui = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
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
