"""Quality controls must preserve existing AI cache and profile semantics."""

from types import SimpleNamespace

from PIL import Image

from superpaper import cloud_upscale


def test_auto_keeps_previous_credit_saving_scale_choices():
    select = cloud_upscale.selected_scale
    assert select((1920, 1080), (1920, 1080), mode="auto") is None
    assert select((960, 540), (1920, 1080), mode="auto") == 2
    assert select((400, 250), (1920, 1080), mode="auto") == 4


def test_explicit_provider_models_include_8x_and_force_processing():
    select = cloud_upscale.selected_scale
    assert select((1920, 1080), (1920, 1080), mode="2") == 2
    assert select((1920, 1080), (1920, 1080), mode="4") == 4
    assert select((1920, 1080), (1920, 1080), mode="8") == 8
    assert select((1920, 1080), (1920, 1080), mode="unknown") is None


def test_sharpen_input_is_bounded_and_invalid_input_is_safe():
    assert cloud_upscale.normalize_sharpen(-25) == 0
    assert cloud_upscale.normalize_sharpen(25) == 25
    assert cloud_upscale.normalize_sharpen(200) == 100
    assert cloud_upscale.normalize_sharpen("not a percentage") == 0


def test_cache_survives_disable_reenable_and_model_switches(tmp_path, monkeypatch):
    source = tmp_path / "original.png"
    image = Image.new("RGB", (40, 24), "orange")
    image.save(source)
    calls = []

    def server(image, scale, cache_dir):
        calls.append(scale)
        generated = cache_dir / f"server-{scale}.png"
        image.resize((image.width * scale, image.height * scale)).save(generated)
        return str(generated)

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", server)
    opts = {"zoom": 1, "cache_root": tmp_path}
    def apply(mode, enabled=True):
        return cloud_upscale.prepare_cloud_upscaled_image(
            image, source, (70, 42), enabled=enabled, scale_mode=mode, **opts
        )

    assert apply("2").size == (80, 48)
    assert apply("4").size == (160, 96)
    assert apply("8").size == (320, 192)
    assert apply("2", enabled=False) is image
    assert apply("2").size == (80, 48)
    assert apply("4").size == (160, 96)
    assert apply("8").size == (320, 192)
    assert calls == [2, 4, 8]


def test_sharpen_from_cached_image_does_not_request_cloud_again(tmp_path, monkeypatch):
    source = tmp_path / "original.png"
    image = Image.new("RGB", (32, 32))
    for y in range(32):
        for x in range(32):
            image.putpixel((x, y), (255 if x > 15 else 0, y * 7, x * 7))
    image.save(source)
    calls = []

    def server(image, scale, cache_dir):
        calls.append(scale)
        generated = cache_dir / "server.png"
        image.resize((64, 64), Image.Resampling.BILINEAR).save(generated)
        return str(generated)

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", server)
    kwargs = {"zoom": 1, "enabled": True, "cache_root": tmp_path, "scale_mode": "2"}
    normal = cloud_upscale.prepare_cloud_upscaled_image(image, source, (64, 64), sharpen=0, **kwargs)
    sharp = cloud_upscale.prepare_cloud_upscaled_image(image, source, (64, 64), sharpen=100, **kwargs)
    normal_again = cloud_upscale.prepare_cloud_upscaled_image(image, source, (64, 64), sharpen=0, **kwargs)
    assert normal.tobytes() == normal_again.tobytes()
    assert normal.tobytes() != sharp.tobytes()
    assert calls == [2]


def test_8x_oversized_job_is_not_uploaded(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    image = Image.new("RGB", (2000, 1000), "black")
    calls = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: calls.append(args))
    result = cloud_upscale.prepare_cloud_upscaled_image(
        image, source, (3000, 1500), zoom=1, enabled=True, cache_root=tmp_path, scale_mode="8"
    )
    assert result is image
    assert calls == []


def test_cloud_model_and_sharpen_persist_while_disabled(profile_modules, tmp_path):
    data, _ = profile_modules
    image = tmp_path / "picture.png"
    Image.new("RGB", (10, 10), "blue").save(image)
    prof = data.TempProfileData()
    prof.name = "options"
    prof.spanmode = "single"
    prof.slideshow = False
    prof.paths_array = [str(image)]
    prof.cloud_upscale = False
    prof.cloud_upscale_scale = "8"
    prof.cloud_upscale_sharpen = 75
    saved = tmp_path / "options.profile"
    prof.save(filename=saved)
    text = saved.read_text(encoding="utf-8")
    assert "cloud_upscale_scale=8" in text
    assert "cloud_upscale_sharpen=75" in text
    reloaded = data.parse_profile_file(saved)
    assert reloaded.cloud_upscale is False
    assert reloaded.cloud_upscale_scale == "8"
    assert reloaded.cloud_upscale_sharpen == 75
    prof.cloud_upscale = True
    prof.save(filename=saved)
    reloaded = data.parse_profile_file(saved)
    assert reloaded.cloud_upscale is True
    assert reloaded.cloud_upscale_scale == "8"
    assert reloaded.cloud_upscale_sharpen == 75


def test_render_passes_cloud_quality_controls(profile_modules, monkeypatch, tmp_path):
    _, wpproc = profile_modules
    source = tmp_path / "picture.png"
    Image.new("RGB", (40, 30), "purple").save(source)
    monkeypatch.setattr(wpproc, "TEMP_PATH", str(tmp_path))
    monkeypatch.setattr(wpproc, "RESOLUTION_ARRAY", [(80, 60)])
    monkeypatch.setattr(wpproc, "DISPLAY_OFFSET_ARRAY", [(0, 0)])
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "options")
    monkeypatch.setattr(wpproc, "set_wallpaper", lambda *args: None)
    kwargs_seen = []

    def capture(image, source, target, **kwargs):
        kwargs_seen.append(kwargs)
        return image

    monkeypatch.setattr(wpproc, "prepare_cloud_upscaled_image", capture)
    profile = SimpleNamespace(
        name="options", zoom=1.25, offsets=(0.0, 0.0),
        cloud_upscale=True, cloud_upscale_scale="8", cloud_upscale_sharpen=60,
        next_wallpaper_files=lambda: [str(source)]
    )
    assert wpproc.span_single_image_simple(profile, force=True) == 0
    assert kwargs_seen[0]["scale_mode"] == "8"
    assert kwargs_seen[0]["sharpen"] == 60
