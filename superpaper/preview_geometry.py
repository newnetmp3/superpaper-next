"""Pure geometry checks for wxPython wallpaper previews.

The GUI sees zero-sized intermediate layouts and bezels thinner than a
preview pixel. Keep those calculations independent of the GUI toolkit so
they can be regression-tested in headless CI.
"""

MIN_PREVIEW_SIDE = 32


class PreviewGeometryError(ValueError):
    """Reject invalid preview dimensions."""


def has_positive_area(size):
    """A wx.Bitmap requires both dimensions to be strictly positive."""
    return size[0] > 0 and size[1] > 0


def usable_preview_area(size):
    """Ignore transient tiny layouts while the preview pane is being sized."""
    return size[0] >= MIN_PREVIEW_SIDE and size[1] >= MIN_PREVIEW_SIDE


def fit_preview_canvas(canvas_size, work_size):
    """Return (scaled size, centered position, scale) with valid pixel sizes."""
    if not has_positive_area(canvas_size) or not has_positive_area(work_size):
        raise PreviewGeometryError

    scale = min(
        0.9 * work_size[0] / canvas_size[0],
        0.9 * work_size[1] / canvas_size[1],
    )
    width = max(1, round(canvas_size[0] * scale))
    height = max(1, round(canvas_size[1] * scale))
    position = (round((work_size[0] - width) / 2), round((work_size[1] - height) / 2))
    return (width, height), position, scale


def crop_overflow(image_size, target_size, zoom):
    """Available crop pixels when the image covers the preview."""
    if not has_positive_area(image_size) or not has_positive_area(target_size):
        raise PreviewGeometryError
    scale = max(target_size[0] / image_size[0], target_size[1] / image_size[1]) * max(1.0, zoom)
    scaled = (
        max(round(image_size[0] * scale), target_size[0]),
        max(round(image_size[1] * scale), target_size[1]),
    )
    return scaled[0] - target_size[0], scaled[1] - target_size[1]


def pan_offset_for_drag(initial, movement, overflow):
    """Move the image with the pointer; clamp normalized crop offsets."""
    return tuple(
        max(-1.0, min(1.0, offset - 2.0 * delta / extra)) if extra > 0 else offset
        for offset, delta, extra in zip(initial, movement, overflow)
    )


def wheel_scroll_units(rotation, delta, lines, remainder=0.0):
    """Convert wheel notches to outer scrollbar units, including partial input."""
    if delta <= 0:
        return 0, remainder
    total = remainder - rotation / delta * max(lines, 1)
    units = int(total)
    return units, total - units
