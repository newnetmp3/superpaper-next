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
