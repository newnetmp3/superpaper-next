"""Optional, cloud-hosted super-resolution for undersized wallpaper sources.

The image is uploaded only when a profile explicitly enables cloud_upscale.
Inference runs on a community Hugging Face Space, not on the user's GPU.
Neither the original images nor the profile's selections are modified.

Public Spaces have limited free compute and no availability guarantees; always
return the original image on failure so wallpaper application still succeeds.
"""

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from PIL import Image

SPACE_ID = "Nick088/Real-ESRGAN_Pytorch"
CACHE_VERSION = "nick088-real-esrgan-v1"
MAX_INPUT_PIXELS = 4_000_000
MAX_INPUT_BYTES = 10_000_000
MAX_OUTPUT_PIXELS = 80_000_000
MAX_OUTPUT_BYTES = 120_000_000
JOB_TIMEOUT_SECONDS = 120

LOGGER = logging.getLogger(__name__)


def recommended_scale(source_size, target_size, zoom=1.0):
    """Select x2 or x4 only when the source would otherwise be magnified."""
    if min(*source_size, *target_size) <= 0:
        return None
    coverage = max(target_size[0] / source_size[0], target_size[1] / source_size[1])
    needed = coverage * max(1.0, zoom)
    if needed <= 1.0:
        return None
    return 2 if needed <= 2.0 else 4


def cache_file_for_source(source_path, cache_root, scale):
    """Cache by identity and source modification metadata, not by profile."""
    stat = os.stat(source_path)
    identity = f"{os.path.realpath(source_path)}:{stat.st_size}:{stat.st_mtime_ns}:{scale}:{CACHE_VERSION}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Path(cache_root) / "cloud-upscale" / f"{digest}.png"


def _usable_image(filename, original_size):
    """Validate remote results before they become trusted wallpaper sources."""
    path = Path(filename)
    if not path.is_file() or path.stat().st_size > MAX_OUTPUT_BYTES:
        return None
    with Image.open(path) as candidate:
        if (
            candidate.width < original_size[0]
            or candidate.height < original_size[1]
            or candidate.width * candidate.height > MAX_OUTPUT_PIXELS
        ):
            return None
        candidate.load()
        return candidate.convert("RGBA" if candidate.mode == "RGBA" else "RGB")


def _request_remote_upscale(image, scale, cache_dir):
    """Upload a normalized image and fetch the enhanced result via Gradio.

    gradio_client is an optional *lightweight client*, not an AI runtime.
    A timeout bounds how long the worker waits for a free Space's quota.
    """
    from gradio_client import Client, handle_file  # ty:ignore[unresolved-import]

    with tempfile.NamedTemporaryFile(suffix=".png", dir=cache_dir, delete=False) as upload:
        upload_path = Path(upload.name)
    try:
        image.convert("RGBA" if image.mode == "RGBA" else "RGB").save(upload_path)
        if upload_path.stat().st_size > MAX_INPUT_BYTES:
            return None
        client = Client(SPACE_ID, verbose=False)
        job = client.submit(handle_file(str(upload_path)), scale, api_name="/predict")
        result = job.result(timeout=JOB_TIMEOUT_SECONDS)
        if isinstance(result, (list, tuple)):
            result = result[0] if result else None
        if isinstance(result, dict):
            result = result.get("path")
        return result if isinstance(result, str) else None
    finally:
        upload_path.unlink(missing_ok=True)


def prepare_cloud_upscaled_image(image, source_path, target_size, *, zoom, enabled, cache_root):
    """Use cached/free cloud AI when needed; return original on any failure.

    Never request the network unless this specific profile opted in.
    Cache hits work offline, and upload errors do not interrupt wallpaper use.
    """
    if not enabled:
        return image
    scale = recommended_scale(image.size, target_size, zoom)
    if scale is None:
        return image
    if image.width * image.height > MAX_INPUT_PIXELS:
        return image
    try:
        cached = cache_file_for_source(source_path, cache_root, scale)
        if cached.is_file():
            enhanced = _usable_image(cached, image.size)
            if enhanced is not None:
                return enhanced
            cached.unlink(missing_ok=True)
        cached.parent.mkdir(parents=True, exist_ok=True)
        remote_file = _request_remote_upscale(image, scale, cached.parent)
        if remote_file is None:
            return image
        enhanced = _usable_image(remote_file, image.size)
        if enhanced is None:
            return image
        # Persist the *validated* result atomically so later renders and
        # slideshows do not spend more limited free GPU quota.
        with tempfile.NamedTemporaryFile(suffix=".png", dir=cached.parent, delete=False) as target:
            stage = Path(target.name)
        try:
            enhanced.save(stage, "PNG")
            os.replace(stage, cached)
        finally:
            stage.unlink(missing_ok=True)
    except Exception as error:
        LOGGER.warning("Cloud upscale unavailable; using original wallpaper: %s", error)
        return image
    else:
        return enhanced
