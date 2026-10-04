"""Local still-image processing with mpv/Anime4K GLSL shader hooks.

FFmpeg's libplacebo Vulkan filter executes the real mpv .glsl code; it is not
approximated by Pillow. No remote service, AI model download, or shell is used.
Shader packs are imported explicitly, with safe, bounded archive extraction.
Missing Vulkan/FFmpeg support is non-fatal: use the unmodified image.
"""

# Import failures are surfaced in the UI with specific actionable messages.
# ruff: noqa: TRY003, EM101

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from superpaper.sp_paths import CONFIG_PATH

LOGGER = logging.getLogger(__name__)
MAX_SHADER_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 16_000_000
MAX_ARCHIVE_FILES = 128
MAX_OUTPUT_PIXELS = 40_000_000
MAX_INPUT_PIXELS = 16_000_000
RENDER_TIMEOUT = 120
_SHADER_RENDER_VARIANTS = ("direct", "hardware-upload")
_SHADER_RE = re.compile(r"^Anime4K_[A-Za-z0-9_-]+[.]glsl$")
_PRESET_RE = re.compile(r"^Anime4K_Mode_[ABC]$")
# The MPV pack contains individual hooks. AutoDownscalePre is not an effect:
# it must run after an Upscale pass, and must not appear in the effect picker.
_PRESET_STAGES = {
    "Anime4K_Mode_A": ("Restore_CNN", "Upscale_CNN_x2"),
    "Anime4K_Mode_B": ("Restore_Soft_CNN", "Upscale_CNN_x2"),
    "Anime4K_Mode_C": ("Upscale_Denoise_CNN_x2",),
}
_VARIANT_ORDER = ("M", "S", "L", "VL", "UL")



class ShaderImportError(ValueError):
    """A shader archive is malformed, unsafe, or contains no supported files."""


def normalize_shader(name):
    """Accept only flat Anime4K shader filenames, never arbitrary paths."""
    if name is None:
        return ""
    value = str(name).strip()
    return value if _SHADER_RE.fullmatch(value) or _PRESET_RE.fullmatch(value) else ""


def shader_directory():
    return Path(CONFIG_PATH) / "local-shaders"


def _candidate_dirs(root=None):
    own = Path(root) if root is not None else shader_directory()
    yield own
    if root is None:
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        yield config / "mpv" / "shaders"


def locate_shader(name, *, shader_root=None):
    name = normalize_shader(name)
    if not name:
        return None
    for folder in _candidate_dirs(shader_root):
        candidate = folder / name
        if candidate.is_file() and candidate.stat().st_size <= MAX_SHADER_BYTES:
            return candidate
    return None


def _installed_hooks(*, shader_root=None):
    """List genuine imported hook files; no profile/preset identifiers."""
    choices = set()
    for folder in _candidate_dirs(shader_root):
        if folder.is_dir():
            choices.update(
                item.name
                for item in folder.iterdir()
                if _SHADER_RE.fullmatch(item.name) and item.is_file() and item.stat().st_size <= MAX_SHADER_BYTES
            )
    return sorted(choices)


def resolve_shader_chain(name, *, shader_root=None):
    """Resolve an MPV-style ordered chain, or a single independent effect.

    The AutoDownscalePre hooks only control resolution between two other
    Anime4K stages. They have no standalone effect and are not valid presets.
    """
    name = normalize_shader(name)
    if not name or "AutoDownscalePre" in name:
        return ()
    if name not in _PRESET_STAGES:
        shader = locate_shader(name, shader_root=shader_root)
        return (shader,) if shader is not None else ()
    hooks = set(_installed_hooks(shader_root=shader_root))
    chosen = []
    for stage in _PRESET_STAGES[name]:
        source = None
        for variant in _VARIANT_ORDER:
            filename = f"Anime4K_{stage}_{variant}.glsl"
            if filename in hooks:
                source = locate_shader(filename, shader_root=shader_root)
                break
        if source is None:
            return ()
        chosen.append(source)
    return tuple(chosen)


def available_shaders(*, shader_root=None):
    """Selectable MPV-style ordered modes and usable individual effects."""
    hooks = _installed_hooks(shader_root=shader_root)
    choices = [name for name in hooks if "AutoDownscalePre" not in name]
    choices.extend(
        name for name in _PRESET_STAGES if resolve_shader_chain(name, shader_root=shader_root)
    )
    return sorted(choices)


def _validate_entry(name, data):
    """Read a shader as plain data, never invoke an archive extraction tool."""
    parts = Path(name).parts
    if ".." in parts or Path(name).is_absolute():
        raise ShaderImportError("Shader archive contains an unsafe path.")
    basename = Path(name).name
    if not normalize_shader(basename):
        return None
    if len(data) > MAX_SHADER_BYTES or b"//!HOOK " not in data:
        raise ShaderImportError("Invalid or excessively large Anime4K shader: " + basename)
    return basename, data


def import_shader_pack(archive, *, shader_root=None):
    """Import .tar.gz, .zip or a single .glsl file; preserve license headers.

    tarfile.extractall and zipfile.extractall are intentionally never used.
    """
    archive = Path(archive)
    found = {}
    total = 0
    if archive.suffix.lower() == ".glsl":
        entries = [(archive.name, archive.read_bytes())]
    elif zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as pack:
            members = [item for item in pack.infolist() if not item.is_dir()]
            if len(members) > MAX_ARCHIVE_FILES:
                raise ShaderImportError("Shader archive contains too many files.")
            entries = []
            for item in members:
                if item.file_size > MAX_SHADER_BYTES:
                    raise ShaderImportError("Archive member exceeds the shader file size limit.")
                entries.append((item.filename, pack.read(item)))
    else:
        try:
            with tarfile.open(archive, mode="r:gz") as pack:
                members = [item for item in pack.getmembers() if item.isfile()]
                if len(members) > MAX_ARCHIVE_FILES:
                    raise ShaderImportError("Shader archive contains too many files.")
                entries = []
                for item in members:
                    if item.size > MAX_SHADER_BYTES:
                        raise ShaderImportError("Archive member exceeds the shader file size limit.")
                    stream = pack.extractfile(item)
                    if stream is not None:
                        entries.append((item.name, stream.read(MAX_SHADER_BYTES + 1)))
        except tarfile.TarError as exc:
            raise ShaderImportError("Not a valid .tar.gz, .zip or Anime4K GLSL shader.") from exc
    for name, data in entries:
        total += len(data)
        if total > MAX_ARCHIVE_BYTES:
            raise ShaderImportError("Shader archive is too large.")
        checked = _validate_entry(name, data)
        if checked is not None:
            basename, content = checked
            if basename in found and found[basename] != content:
                raise ShaderImportError("Duplicate shader names contain different data.")
            found[basename] = content
    if not found:
        raise ShaderImportError("No supported Anime4K .glsl shader hooks were found.")
    destination = Path(shader_root) if shader_root is not None else shader_directory()
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in found.items():
        with tempfile.NamedTemporaryFile(dir=destination, suffix=".tmp", delete=False) as stage:
            temp = Path(stage.name)
            stage.write(content)
        try:
            os.replace(temp, destination / name)
        finally:
            temp.unlink(missing_ok=True)
    return len(found)


def shader_output_size(image_size, target_size, zoom, shader_name):
    """Run x2 upscalers against a larger output; never distort image aspect."""
    if not shader_name or not all(v > 0 for v in (*image_size, *target_size)):
        return image_size
    # Anime4K Upscale hooks are conditioned on OUTPUT being larger than MAIN.
    scale = 2 if "_Upscale_" in shader_name or shader_name in _PRESET_STAGES else 1
    return image_size[0] * scale, image_size[1] * scale


def shader_filtergraph(dimensions, hook, variant):
    """Build a Vulkan libplacebo graph compatible with recent and older FFmpeg.

    Modern libplacebo handles software-frame upload/output itself. The older
    hwupload/hwdownload route is kept as a fallback for distro FFmpeg builds
    which require explicit hardware frames.
    """
    width, height = dimensions
    effect = f"libplacebo=w={width}:h={height}:custom_shader_path={hook}"
    if variant == "direct":
        return effect + ":format=rgb24,format=rgb24"
    if variant == "hardware-upload":
        return f"format=rgba,hwupload,{effect},hwdownload,format=rgba"
    raise ValueError("Unrecognized shader filter mode")


def shader_error_detail(stderr):
    """Preserve the first useful Vulkan/GLSL errors, not only FFmpeg's footer."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "No FFmpeg diagnostic output; verify FFmpeg libplacebo and Vulkan support."
    return "\n".join((lines[:12] + lines[-4:]) if len(lines) > 16 else lines)[:4000]


def apply_image_shader(image, name, target_size, *, zoom=1.0, cache_root=None, shader_root=None):
    """Apply the chosen shader on the local Vulkan GPU; fail open to image.

    Output is cached by source pixel bytes, shader bytes and target geometry.
    Does not change Cloud AI cache entries or source-relative crop settings.
    """
    name = normalize_shader(name)
    if not name:
        return image
    shaders = resolve_shader_chain(name, shader_root=shader_root)
    if not shaders:
        LOGGER.warning(
            "Anime4K effect %s is unavailable or a pipeline-only helper. "
            "Select a complete Mode A/B/C preset, or another standalone effect.",
            name,
        )
        return image
    dimensions = shader_output_size(image.size, target_size, zoom, name)
    if image.width * image.height > MAX_INPUT_PIXELS or dimensions[0] * dimensions[1] > MAX_OUTPUT_PIXELS:
        LOGGER.warning("Skipping local shader on oversized image.")
        return image
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        LOGGER.warning("FFmpeg not installed; local image shaders unavailable.")
        return image
    try:
        digest = hashlib.sha256()
        digest.update(image.convert("RGB").tobytes())
        for shader in shaders:
            digest.update(shader.name.encode())
            digest.update(shader.read_bytes())
        digest.update(f"{name}:{dimensions}:libplacebo-mpv-chain-v1".encode())
        cache = Path(cache_root) if cache_root is not None else Path(tempfile.gettempdir())
        cache = cache / "local-shader-output" / (digest.hexdigest() + ".png")
        if cache.is_file():
            with Image.open(cache) as loaded:
                if loaded.size == dimensions:
                    loaded.load()
                    return loaded.convert("RGB")
            cache.unlink(missing_ok=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="superpaper-anime4k-") as folder:
            work = Path(folder)
            src = work / "input.png"
            output = work / "output.png"
            # A short, controlled path prevents FFmpeg filtergraph injections
            # from special characters in profile or user directory names.
            hook = work / "shader.glsl"
            # libplacebo parses MPV's //!HOOK blocks. Concatenating their
            # complete text retains each pass and its defined execution order.
            hook.write_bytes(b"\n\n".join(shader.read_bytes() for shader in shaders) + b"\n")
            image.convert("RGB").save(src)
            # Attempt the modern direct libplacebo software-frame input
            # first. Not all FFmpeg builds support the explicit hwupload path
            # previously used (which can fail during filter initialization).
            rendered = False
            for variant in _SHADER_RENDER_VARIANTS:
                output.unlink(missing_ok=True)
                command = [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "verbose",
                    "-y",
                    "-init_hw_device",
                    "vulkan=vk",
                    "-filter_hw_device",
                    "vk",
                    "-i",
                    str(src),
                    "-vf",
                    shader_filtergraph(dimensions, hook, variant),
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(output),
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=RENDER_TIMEOUT)
                if result.returncode == 0 and output.is_file():
                    rendered = True
                    break
                LOGGER.warning(
                    "Anime4K shader %s (%s) failed (FFmpeg exit %s): %s",
                    name,
                    variant,
                    result.returncode,
                    shader_error_detail(result.stderr),
                )
            if not rendered:
                LOGGER.warning(
                    "Anime4K shader %s could not render using either FFmpeg path. "
                    "Check ffmpeg -h filter=libplacebo for custom_shader_path and verify Vulkan via vulkaninfo.",
                    name,
                )
                return image
            with Image.open(output) as rendered:
                if rendered.size != dimensions:
                    LOGGER.warning("Shader output dimensions differ from requested size.")
                    return image
                rendered.load()
                enhanced = rendered.convert("RGB")
            with tempfile.NamedTemporaryFile(dir=cache.parent, suffix=".png", delete=False) as stage:
                temporary = Path(stage.name)
            try:
                enhanced.save(temporary, "PNG")
                os.replace(temporary, cache)
            finally:
                temporary.unlink(missing_ok=True)
            return enhanced
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        LOGGER.warning("Local Anime4K shader unavailable; using original image: %s", exc)
        return image
