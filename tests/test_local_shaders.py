"""Anime4K image shaders are local, opt-in, path-safe and cacheable."""

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from superpaper import local_shaders

HOOK = b"// MIT License\n//!HOOK MAIN\n//!BIND HOOKED\nvec4 hook() { return HOOKED_tex(HOOKED_pos); }\n"
NAME = "Anime4K_Restore_CNN_S.glsl"


def _archive(path, extra=None):
    entries = {f"shaders/{NAME}": HOOK}
    entries.update(extra or {})
    with tarfile.open(path, "w:gz") as pack:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            pack.addfile(info, io.BytesIO(data))


def test_archive_installs_shader_without_extracting_paths(tmp_path):
    pack = tmp_path / "pack.tar.gz"
    _archive(pack)
    shader_dir = tmp_path / "installed"
    assert local_shaders.import_shader_pack(pack, shader_root=shader_dir) == 1
    assert local_shaders.available_shaders(shader_root=shader_dir) == [NAME]
    assert (shader_dir / NAME).read_bytes() == HOOK


def test_archive_rejects_traversal_before_writing(tmp_path):
    pack = tmp_path / "malicious.tar.gz"
    _archive(pack, {"../Anime4K_Outside.glsl": HOOK})
    with pytest.raises(local_shaders.ShaderImportError):
        local_shaders.import_shader_pack(pack, shader_root=tmp_path / "installed")
    assert not (tmp_path / "installed").exists()
    assert not (tmp_path / "Anime4K_Outside.glsl").exists()


def test_archive_rejects_oversized_member(tmp_path):
    pack = tmp_path / "too-large.tar.gz"
    _archive(pack, {f"shaders/{NAME}": HOOK * 15000})
    with pytest.raises(local_shaders.ShaderImportError):
        local_shaders.import_shader_pack(pack, shader_root=tmp_path / "installed")


def test_reject_filename_injection():
    assert local_shaders.normalize_shader("../../Anime4K_Bad.glsl") == ""
    assert local_shaders.normalize_shader("Anime4K_Bad.glsl:scale=999") == ""
    assert local_shaders.normalize_shader("other.glsl") == ""
    assert local_shaders.normalize_shader(NAME) == NAME


def test_shader_upscalers_use_2x_original_dimensions():
    assert local_shaders.shader_output_size((300, 150), (1920, 1080), 1, "Anime4K_Upscale_CNN_x2_S.glsl") == (600, 300)
    assert local_shaders.shader_output_size((300, 150), (1920, 1080), 1, NAME) == (300, 150)


def test_unselected_or_not_imported_shader_does_not_use_ffmpeg(tmp_path, monkeypatch):
    image = Image.new("RGB", (40, 20), "pink")
    calls = []
    monkeypatch.setattr(local_shaders.subprocess, "run", lambda *a, **kw: calls.append(a))
    assert local_shaders.apply_image_shader(image, "", (80, 40), cache_root=tmp_path) is image
    assert local_shaders.apply_image_shader(image, NAME, (80, 40), shader_root=tmp_path, cache_root=tmp_path) is image
    assert not calls


def test_shader_command_uses_ffmpeg_libplacebo_and_reuses_cache(tmp_path, monkeypatch):
    installed = tmp_path / "shaderpack"
    installed.mkdir()
    (installed / NAME).write_bytes(HOOK)
    monkeypatch.setattr(local_shaders.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    commands = []

    def render(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == local_shaders.RENDER_TIMEOUT
        assert kwargs["check"] is False
        assert "-init_hw_device" in command
        assert "vulkan=vk" in command
        assert "-filter_hw_device" in command
        assert "custom_shader_path=" in " ".join(command)
        assert "libplacebo=w=40:h=20:" in command[command.index("-vf") + 1]
        output = Path(command[-1])
        Image.new("RGB", (40, 20), "green").save(output)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(local_shaders.subprocess, "run", render)
    image = Image.new("RGB", (40, 20), "pink")
    kwargs = {"shader_root": installed, "cache_root": tmp_path}
    first = local_shaders.apply_image_shader(image, NAME, (80, 40), **kwargs)
    second = local_shaders.apply_image_shader(image, NAME, (80, 40), **kwargs)
    assert first.getpixel((5, 5)) == (0, 128, 0)
    assert second.tobytes() == first.tobytes()
    assert len(commands) == 1
    # Editing the shader invalidates its cache, even if the input is unchanged.
    (installed / NAME).write_bytes(HOOK + b"// revision\n")
    local_shaders.apply_image_shader(image, NAME, (80, 40), **kwargs)
    assert len(commands) == 2


def test_shader_failure_returns_original_without_corrupt_cache(tmp_path, monkeypatch):
    installed = tmp_path / "shaderpack"
    installed.mkdir()
    (installed / NAME).write_bytes(HOOK)
    monkeypatch.setattr(local_shaders.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        local_shaders.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stderr="GPU not supported"),
    )
    image = Image.new("RGB", (50, 30), "purple")
    output = local_shaders.apply_image_shader(image, NAME, (80, 40), shader_root=installed, cache_root=tmp_path)
    assert output is image
    assert not list(tmp_path.rglob("*.png"))


def test_profile_roundtrip_remembers_shader_even_without_cloud(profile_modules, tmp_path):
    data, _wpproc = profile_modules
    source = tmp_path / "wall.png"
    Image.new("RGB", (30, 20), "orange").save(source)
    profile = data.TempProfileData()
    profile.name = "shader-test"
    profile.spanmode = "single"
    profile.slideshow = False
    profile.cloud_upscale = False
    profile.local_shader = NAME
    profile.paths_array = [str(source)]
    output = tmp_path / "shader-test.profile"
    profile.save(filename=output)
    saved = data.parse_profile_file(output)
    assert saved.cloud_upscale is False
    assert saved.local_shader == NAME


def test_real_render_wires_selected_shader_without_cloud(profile_modules, tmp_path, monkeypatch):
    _, wpproc = profile_modules
    source = tmp_path / "wall.png"
    Image.new("RGB", (30, 20), "orange").save(source)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(wpproc, "TEMP_PATH", str(cache))
    monkeypatch.setattr(wpproc, "RESOLUTION_ARRAY", [(60, 40)])
    monkeypatch.setattr(wpproc, "DISPLAY_OFFSET_ARRAY", [(0, 0)])
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "shader-test")
    monkeypatch.setattr(wpproc, "set_wallpaper", lambda *args: None)
    calls = []

    def fake_shader(image, name, target, **kwargs):
        calls.append((name, target))
        return image

    monkeypatch.setattr(wpproc, "apply_image_shader", fake_shader)
    profile = SimpleNamespace(
        name="shader-test",
        zoom=1.25,
        offsets=(0.2, -0.3),
        cloud_upscale=False,
        local_shader=NAME,
        next_wallpaper_files=lambda: [str(source)],
    )
    assert wpproc.span_single_image_simple(profile, force=True) == 0
    assert calls == [(NAME, (60, 40))]
