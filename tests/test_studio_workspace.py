"""Studio workspace navigation and image adjustment regressions."""

import ast
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from superpaper.image_adjustments import apply_local_adjustments, normalize_adjustment


def _colorful_source(size=(80, 48)):
    image = Image.new("RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (20 + x * 2, 20 + y * 4, 100 + x))
    return image


def _class(name):
    path = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _method(class_name, method):
    node = _class(class_name)
    return next(child for child in node.body if isinstance(child, ast.FunctionDef) and child.name == method)


def test_studio_workspaces_reuse_real_widgets_and_callbacks():
    panel = _class("WallpaperSettingsPanel")
    constructor = _method("WallpaperSettingsPanel", "__init__")
    text = ast.unparse(constructor)
    assert "self.create_studio_navigation()" in text
    assert "self.create_studio_gallery()" in text
    for section in ("Wallpapers", "Displays", "Profiles", "Processing", "Advanced"):
        assert section in text
    assert "self.sizer_bottom_half.Add(self.sizer_top_half" in text
    assert "self.sizer_bottom_half.Add(self.studio_preview_tools" in text
    assert "self.sizer_bottom_half.Add(self.sizer_bottom_buttonrow" in text
    methods = {node.name for node in panel.body if isinstance(node, ast.FunctionDef)}
    for name in (
        "_set_studio_workspace",
        "_studio_toggle_compare",
        "_studio_choose_monitor",
        "_refresh_profile_gallery",
        "_on_gallery_selected",
        "_studio_new_profile",
        "_studio_duplicate_profile",
    ):
        assert name in methods


def test_switching_workspace_preserves_edits_and_refits_scroll():
    source = ast.unparse(_method("WallpaperSettingsPanel", "_set_studio_workspace"))
    assert "self.sizer_bottom_half.Show(section, show=key == name)" in source
    assert "self.FitInside()" in source
    assert "self.Scroll(0, 0)" in source
    assert "self.populate_fields(" not in source


def test_preview_split_is_local_and_does_not_use_gpu_or_cloud():
    panel = _class("WallpaperPreviewPanel")
    render = [
        node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "resize_and_bitmap"
    ][-1]
    source = ast.unparse(render)
    assert "self.compare_original" in source
    assert "plain.crop(" in source
    assert "apply_local_adjustments(" in source
    assert "prepare_cloud_upscaled_image(" not in source
    assert "apply_image_shader(" not in source


def test_local_adjustment_identity_and_range_clamp():
    image = _colorful_source()
    assert apply_local_adjustments(image) is image
    assert normalize_adjustment("bad") == 0
    assert normalize_adjustment(-300) == -100
    assert normalize_adjustment(999) == 100


def test_local_brightness_contrast_and_saturation_change_pixels():
    original = _colorful_source()
    for options in (
        {"brightness": -70},
        {"contrast": 70},
        {"saturation": -100},
    ):
        altered = apply_local_adjustments(original, **options)
        assert altered.size == original.size
        assert altered.tobytes() != original.tobytes()


def test_local_adjustment_profile_round_trip_without_cloud(profile_modules, tmp_path):
    data, _wpproc = profile_modules
    source = tmp_path / "source.png"
    _colorful_source().save(source)
    profile = data.TempProfileData()
    profile.name = "studio"
    profile.spanmode = "single"
    profile.slideshow = False
    profile.cloud_upscale = False
    profile.local_brightness = -18
    profile.local_contrast = 36
    profile.local_saturation = -60
    profile.paths_array = [str(source)]
    destination = tmp_path / "studio.profile"
    profile.save(filename=destination)
    loaded = data.parse_profile_file(destination)
    assert loaded.cloud_upscale is False
    assert loaded.local_brightness == -18
    assert loaded.local_contrast == 36
    assert loaded.local_saturation == -60


def test_tone_changes_real_wallpaper_without_ai_or_shaders(profile_modules, tmp_path, monkeypatch):
    _, processing = profile_modules
    source = tmp_path / "source.png"
    _colorful_source().save(source)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(processing, "TEMP_PATH", str(cache))
    monkeypatch.setattr(processing, "RESOLUTION_ARRAY", [(80, 48)])
    monkeypatch.setattr(processing, "DISPLAY_OFFSET_ARRAY", [(0, 0)])
    monkeypatch.setattr(processing, "G_ACTIVE_PROFILE", "studio")
    monkeypatch.setattr(processing, "set_wallpaper", lambda *args: None)
    profile = SimpleNamespace(
        name="studio",
        zoom=1.0,
        offsets=(0.0, 0.0),
        cloud_upscale=False,
        cloud_upscale_sharpen=0,
        local_shader="",
        local_brightness=0,
        local_contrast=0,
        local_saturation=0,
        next_wallpaper_files=lambda: [str(source)],
    )
    assert processing.span_single_image_simple(profile, force=True) == 0
    with Image.open(cache / "studio-a.png") as result:
        baseline = result.convert("RGB").tobytes()
    profile.local_saturation = -100
    assert processing.span_single_image_simple(profile, force=True) == 0
    with Image.open(cache / "studio-b.png") as result:
        changed = result.convert("RGB").tobytes()
    assert changed != baseline


def test_old_profile_defaults_are_neutral(profile_modules, tmp_path):
    data, _wpproc = profile_modules
    source = tmp_path / "source.png"
    _colorful_source().save(source)
    profile = data.TempProfileData()
    profile.name = "older"
    profile.spanmode = "single"
    profile.paths_array = [str(source)]
    file = tmp_path / "older.profile"
    profile.save(filename=file)
    text = file.read_text(encoding="utf-8")
    assert "local_brightness=" not in text
    assert "local_contrast=" not in text
    assert "local_saturation=" not in text
    loaded = data.parse_profile_file(file)
    assert (loaded.local_brightness, loaded.local_contrast, loaded.local_saturation) == (0, 0, 0)
