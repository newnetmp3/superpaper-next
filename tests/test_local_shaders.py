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


def test_libplacebo_graph_prefers_software_frames_without_manual_hwupload():
    hook = Path("/tmp/shader.glsl")
    direct = local_shaders.shader_filtergraph((120, 80), hook, "direct")
    assert direct.startswith("libplacebo=w=120:h=80:custom_shader_path=")
    assert "hwupload" not in direct
    assert direct.endswith("format=rgb24,format=rgb24")
    fallback = local_shaders.shader_filtergraph((120, 80), hook, "hardware-upload")
    assert "format=rgba,hwupload,libplacebo=" in fallback
    assert fallback.endswith("hwdownload,format=rgba")
    with pytest.raises(ValueError):
        local_shaders.shader_filtergraph((120, 80), hook, "invalid")


def test_shader_tries_compatibility_graph_after_filter_failure(tmp_path, monkeypatch):
    shader_root = tmp_path / "shader"
    shader_root.mkdir()
    (shader_root / NAME).write_bytes(HOOK)
    monkeypatch.setattr(local_shaders.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    commands = []

    def render(command, **kwargs):
        commands.append(command)
        graph = command[command.index("-vf") + 1]
        if len(commands) == 1:
            assert "hwupload" not in graph
            return SimpleNamespace(returncode=1, stderr="[AVFilterGraph] Error initializing filters")
        assert "hwupload" in graph
        Image.new("RGB", (32, 24), "green").save(command[-1])
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(local_shaders.subprocess, "run", render)
    source = Image.new("RGB", (32, 24), "red")
    kwargs = {"shader_root": shader_root, "cache_root": tmp_path / "cache"}
    output = local_shaders.apply_image_shader(source, NAME, (64, 48), **kwargs)
    assert output.getpixel((0, 0)) == (0, 128, 0)
    assert len(commands) == 2
    cached = local_shaders.apply_image_shader(source, NAME, (64, 48), **kwargs)
    assert cached.tobytes() == output.tobytes()
    assert len(commands) == 2


def test_error_detail_keeps_initial_filter_failure_not_only_footer():
    messages = ["[AVFilterGraph] Vulkan format negotiation failed"]
    messages.extend(f"verbose detail {i}" for i in range(32))
    messages.append("Error opening output file: Invalid argument")
    result = local_shaders.shader_error_detail("\n".join(messages))
    assert "Vulkan format negotiation failed" in result
    assert "Invalid argument" in result
    assert len(result) < 4001


def test_mpv_pipeline_helpers_are_not_standalone_preset_options(tmp_path):
    shader_root = tmp_path / "pack"
    shader_root.mkdir()
    (shader_root / "Anime4K_AutoDownscalePre_x2.glsl").write_bytes(HOOK)
    (shader_root / NAME).write_bytes(HOOK)
    choices = local_shaders.available_shaders(shader_root=shader_root)
    assert NAME in choices
    assert "Anime4K_AutoDownscalePre_x2.glsl" not in choices
    assert local_shaders.resolve_shader_chain(
        "Anime4K_AutoDownscalePre_x2.glsl", shader_root=shader_root
    ) == ()


def test_preset_modes_resolve_ordered_real_mpv_files(tmp_path):
    folder = tmp_path / "shaders"
    folder.mkdir()
    for name in (
        "Anime4K_Restore_CNN_M.glsl",
        "Anime4K_Restore_CNN_Soft_M.glsl",
        "Anime4K_Upscale_CNN_x2_M.glsl",
        "Anime4K_Upscale_Denoise_CNN_x2_M.glsl",
    ):
        (folder / name).write_bytes(HOOK)
    for mode, expected in (
        ("Anime4K_Mode_A", ["Restore_CNN_M", "Upscale_CNN_x2_M"]),
        ("Anime4K_Mode_B", ["Restore_CNN_Soft_M", "Upscale_CNN_x2_M"]),
        ("Anime4K_Mode_C", ["Upscale_Denoise_CNN_x2_M"]),
    ):
        stages = local_shaders.resolve_shader_chain(mode, shader_root=folder)
        assert [p.name.removeprefix("Anime4K_").removesuffix(".glsl") for p in stages] == expected
        assert mode in local_shaders.available_shaders(shader_root=folder)
        assert local_shaders.normalize_shader(mode) == mode
        assert local_shaders.shader_output_size((40, 20), (80, 40), 1, mode) == (80, 40)
    (folder / "Anime4K_Upscale_CNN_x2_M.glsl").unlink()
    assert "Anime4K_Mode_A" not in local_shaders.available_shaders(shader_root=folder)
    assert "Anime4K_Mode_B" not in local_shaders.available_shaders(shader_root=folder)
    assert "Anime4K_Mode_C" in local_shaders.available_shaders(shader_root=folder)


def test_shader_mode_composes_mpv_hook_passes_in_order_and_caches(tmp_path, monkeypatch):
    shader_root = tmp_path / "shaderpack"
    shader_root.mkdir()
    restore = shader_root / "Anime4K_Restore_CNN_M.glsl"
    upscale = shader_root / "Anime4K_Upscale_CNN_x2_M.glsl"
    restore.write_bytes(HOOK + b"// restore\n")
    upscale.write_bytes(HOOK + b"// upscale\n")
    monkeypatch.setattr(local_shaders.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    calls = []

    def render(command, **kwargs):
        assert "-vf" in command
        assert "custom_shader_path=" in command[command.index("-vf") + 1]
        shader_path = Path(command[-1]).parent / "shader.glsl"
        data = shader_path.read_bytes()
        assert data.index(b"// restore") < data.index(b"// upscale")
        assert data.count(b"//!HOOK MAIN") == 2
        calls.append(command)
        Image.new("RGB", (64, 48), "blue").save(command[-1])
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(local_shaders.subprocess, "run", render)
    image = Image.new("RGB", (32, 24), "pink")
    processed = local_shaders.apply_image_shader(
        image,
        "Anime4K_Mode_A",
        (64, 48),
        cache_root=tmp_path / "cache",
        shader_root=shader_root,
    )
    assert processed.size == (64, 48)
    assert len(calls) == 1
    again = local_shaders.apply_image_shader(
        image,
        "Anime4K_Mode_A",
        (64, 48),
        cache_root=tmp_path / "cache",
        shader_root=shader_root,
    )
    assert again.tobytes() == processed.tobytes()
    assert len(calls) == 1


def test_ffmpeg_failure_diagnoses_vulkan_without_anime4k(tmp_path, monkeypatch):
    installed = tmp_path / "shaderpack"
    installed.mkdir()
    (installed / NAME).write_bytes(HOOK)
    monkeypatch.setattr(local_shaders.shutil, "which", lambda binary: "/usr/bin/ffmpeg")
    commands = []

    def render(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=234, stderr="Vulkan ICD unavailable\nFilter graph error")

    monkeypatch.setattr(local_shaders.subprocess, "run", render)
    with pytest.raises(ValueError):
        local_shaders._validate_entry("Anime4K_Mode_A", HOOK)
    source = Image.new("RGB", (30, 20), "pink")
    assert local_shaders.apply_image_shader(
        source, NAME, (60, 40), shader_root=installed, cache_root=tmp_path
    ) is source
    assert len(commands) == 3
    assert "color=c=gray:s=64x64:d=0.1" in commands[-1]
    assert "libplacebo=w=64:h=64" in commands[-1]
