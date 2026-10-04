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
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageEnhance

SPACE_ID = "Nick088/Real-ESRGAN_Pytorch"
CACHE_VERSION = "nick088-real-esrgan-v1"
MAX_INPUT_PIXELS = 4_000_000
MAX_INPUT_BYTES = 10_000_000
MAX_OUTPUT_PIXELS = 80_000_000
MAX_OUTPUT_BYTES = 120_000_000
JOB_TIMEOUT_SECONDS = 120

LOGGER = logging.getLogger(__name__)
UPSCALE_MODES = ("auto", "2", "4", "8")
_CACHE_MUTEX = threading.Lock()
_CACHE_REQUEST_LOCKS = weakref.WeakValueDictionary()


def normalize_scale_mode(mode):
    """Validate provider-supported factors; keep older profiles on Auto."""
    value = str(mode).strip().lower()
    return value if value in UPSCALE_MODES else "auto"


def normalize_sharpen(amount):
    """Local sharpness percentage from 0 (unchanged) to 100 (strong)."""
    try:
        return max(0, min(100, int(amount)))
    except TypeError, ValueError:
        return 0


def selected_scale(source_size, target_size, zoom=1.0, *, mode="auto"):
    """Choose provider x2/x4/x8; Auto retains the previous credit-saving behavior."""
    normalized = normalize_scale_mode(mode)
    if normalized == "auto":
        return recommended_scale(source_size, target_size, zoom)
    return int(normalized) if min(*source_size, *target_size) > 0 else None


def locally_sharpen(image, amount):
    """Sharpen a cached AI result without another cloud request."""
    strength = normalize_sharpen(amount)
    if not strength:
        return image
    return ImageEnhance.Sharpness(image).enhance(1.0 + strength / 100)


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


@contextmanager
def _upscale_cache_lock(cached):
    """Serialize misses for the same cloud result across threads and POSIX processes.

    The separate lock file must persist: unlinking it would allow waiting
    processes to lock different inodes and both submit paid API requests.
    Weak references avoid retaining a Python mutex for every slideshow image.
    """
    key = str(cached)
    with _CACHE_MUTEX:
        request_lock = _CACHE_REQUEST_LOCKS.get(key)
        if request_lock is None:
            request_lock = threading.Lock()
            _CACHE_REQUEST_LOCKS[key] = request_lock

    with request_lock:
        if os.name != "posix":
            # In-process protection on Windows; POSIX also protects separate
            # processes using an advisory lock on the same cache key.
            yield
            return
        import fcntl

        with cached.with_suffix(".lock").open("a+b") as lockfile:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


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
        # AI outputs must represent the complete original image, not a
        # cropped or letterboxed scene that would invalidate saved framing.
        x_scale = candidate.width / original_size[0]
        y_scale = candidate.height / original_size[1]
        if abs(x_scale - y_scale) > 0.01 * max(x_scale, y_scale):
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


def prepare_cloud_upscaled_image(
    image, source_path, target_size, *, zoom, enabled, cache_root, scale_mode="auto", sharpen=0
):
    """Apply optional AI, then sharpen locally regardless of AI availability.

    Never request the network unless this specific profile opted in.
    On disabled/failed/skipped cloud inference, sharpen the original image.
    Cache hits work offline and sharpening is not part of the cloud cache key.
    """
    if not enabled:
        return locally_sharpen(image, sharpen)
    scale = selected_scale(image.size, target_size, zoom, mode=scale_mode)
    if scale is None or image.width * image.height > MAX_INPUT_PIXELS:
        return locally_sharpen(image, sharpen)
    # The x8 model can produce enormous bitmaps. Avoid spending remote GPU
    # time on outputs we would have to reject for exceeding memory limits.
    if image.width * image.height * scale * scale > MAX_OUTPUT_PIXELS:
        return locally_sharpen(image, sharpen)
    try:
        cached = cache_file_for_source(source_path, cache_root, scale)
        cached.parent.mkdir(parents=True, exist_ok=True)
        with _upscale_cache_lock(cached):
            # Check *after* acquiring the lock: another renderer may have just
            # paid for and committed the exact same image while we waited.
            if cached.is_file():
                enhanced = _usable_image(cached, image.size)
                if enhanced is not None:
                    return locally_sharpen(enhanced, sharpen)
                cached.unlink(missing_ok=True)
            remote_file = _request_remote_upscale(image, scale, cached.parent)
            if remote_file is None:
                return locally_sharpen(image, sharpen)
            enhanced = _usable_image(remote_file, image.size)
            if enhanced is None:
                return locally_sharpen(image, sharpen)
            # Persist the validated result before releasing the request lock.
            with tempfile.NamedTemporaryFile(suffix=".png", dir=cached.parent, delete=False) as target:
                stage = Path(target.name)
            try:
                enhanced.save(stage, "PNG")
                os.replace(stage, cached)
            finally:
                stage.unlink(missing_ok=True)
    except Exception as error:
        LOGGER.warning("Cloud upscale unavailable; using original wallpaper: %s", error)
        return locally_sharpen(image, sharpen)
    else:
        return locally_sharpen(enhanced, sharpen)
