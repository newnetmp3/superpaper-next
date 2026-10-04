"""Cloud super-resolution must be opt-in, cached and failure-tolerant."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

from PIL import Image

from superpaper import cloud_upscale


def _picture(size=(100, 60)):
    return Image.new("RGB", size, (20, 40, 60))


def test_skip_if_source_covers_display():
    assert cloud_upscale.recommended_scale((3840, 2160), (1920, 1080)) is None
    assert cloud_upscale.recommended_scale((960, 540), (1920, 1080)) == 2
    assert cloud_upscale.recommended_scale((400, 300), (3840, 2160)) == 4
    assert cloud_upscale.recommended_scale((1920, 1080), (1920, 1080), zoom=1.25) == 2


def test_disabled_never_uploads(tmp_path, monkeypatch):
    image = _picture()
    calls = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: calls.append(args))
    output = cloud_upscale.prepare_cloud_upscaled_image(
        image, tmp_path / "missing.png", (1920, 1080), zoom=1, enabled=False, cache_root=tmp_path
    )
    assert output is image
    assert calls == []


def test_cache_reused_across_renamed_profiles(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    image = _picture()
    image.save(source)
    uploads = []

    def fake_request(image, scale, cache_dir):
        uploads.append(scale)
        output = cache_dir / "server-output.png"
        image.resize((image.width * scale, image.height * scale)).save(output)
        return str(output)

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", fake_request)
    kwargs = {"zoom": 1, "enabled": True, "cache_root": tmp_path}
    first = cloud_upscale.prepare_cloud_upscaled_image(image, source, (180, 100), **kwargs)
    second = cloud_upscale.prepare_cloud_upscaled_image(image, source, (180, 100), **kwargs)
    assert first.size == second.size == (200, 120)
    assert uploads == [2]


def test_parallel_renders_share_one_remote_upscale_request(tmp_path, monkeypatch):
    """Concurrent renders of the same source must not spend two API requests."""
    source = tmp_path / "source.png"
    image = _picture()
    image.save(source)
    starts = Barrier(2)
    access = Lock()
    calls = []

    def fake_request(picture, scale, cache_dir):
        with access:
            calls.append(scale)
        # Hold the cache miss open long enough for the second render to enter.
        sleep(0.1)
        output = cache_dir / "remote-output.png"
        picture.resize((picture.width * scale, picture.height * scale)).save(output)
        return str(output)

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", fake_request)

    def render(_index):
        starts.wait(timeout=5)
        return cloud_upscale.prepare_cloud_upscaled_image(
            image, source, (180, 100), zoom=1, enabled=True, cache_root=tmp_path
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(render, range(2)))

    assert [result.size for result in outputs] == [(200, 120), (200, 120)]
    assert calls == [2]


def test_offline_or_bad_remote_result_falls_back(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    image = _picture()
    image.save(source)

    def down(*args):
        raise ConnectionError

    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", down)
    output = cloud_upscale.prepare_cloud_upscaled_image(
        image, source, (400, 240), zoom=1, enabled=True, cache_root=tmp_path
    )
    assert output is image


def test_bad_remote_image_not_cached(tmp_path, monkeypatch):
    source = tmp_path / "src.png"
    source.write_bytes(b"any bytes")
    image = _picture()
    bogus = tmp_path / "fake.txt"
    bogus.write_text("invalid PNG")
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: str(bogus))
    output = cloud_upscale.prepare_cloud_upscaled_image(
        image, source, (250, 150), zoom=1, enabled=True, cache_root=tmp_path
    )
    assert output is image
    cached = cloud_upscale.cache_file_for_source(source, tmp_path, 4)
    assert not cached.exists()


def test_input_limit_avoids_upload(tmp_path, monkeypatch):
    image = _picture((2500, 2000))
    called = []
    monkeypatch.setattr(cloud_upscale, "_request_remote_upscale", lambda *args: called.append(1))
    out = cloud_upscale.prepare_cloud_upscaled_image(
        image, tmp_path / "source.png", (6000, 4000), zoom=1, enabled=True, cache_root=tmp_path
    )
    assert out is image
    assert not called


def test_profile_render_invokes_enabled_upscale(profile_modules, monkeypatch, tmp_path):
    _, wpproc = profile_modules
    src = tmp_path / "wallpaper.png"
    _picture().save(src)
    monkeypatch.setattr(wpproc, "TEMP_PATH", str(tmp_path))
    monkeypatch.setattr(wpproc, "RESOLUTION_ARRAY", [(200, 120)])
    monkeypatch.setattr(wpproc, "DISPLAY_OFFSET_ARRAY", [(0, 0)])
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "upscale-test")
    monkeypatch.setattr(wpproc, "set_wallpaper", lambda *args: None)
    calls = []

    def fake_upscale(image, source, size, **kwargs):
        calls.append((source, size, kwargs["enabled"]))
        return image

    monkeypatch.setattr(wpproc, "prepare_cloud_upscaled_image", fake_upscale)
    profile = SimpleNamespace(
        name="upscale-test",
        zoom=1.0,
        offsets=(0.0, 0.0),
        cloud_upscale=True,
        next_wallpaper_files=lambda: [str(src)],
    )
    assert wpproc.span_single_image_simple(profile, force=True) == 0
    assert calls == [(str(src), (200, 120), True)]
