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
        0.94 * work_size[0] / canvas_size[0],
        0.94 * work_size[1] / canvas_size[1],
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


def original_frame_box(source_size, target_size, zoom, offset):
    """Crop coordinates in the ORIGINAL image's pixel space.

    The zoom and normalized pan settings remain attached to the source image,
    independent of any later enhancement or output resolution. The result can
    be mapped into a uniformly enhanced image using its size ratios.
    """
    if not has_positive_area(source_size) or not has_positive_area(target_size):
        raise PreviewGeometryError
    source_w, source_h = source_size
    target_w, target_h = target_size
    zoom = max(1.0, float(zoom))
    offset_x = max(-1.0, min(1.0, float(offset[0])))
    offset_y = max(-1.0, min(1.0, float(offset[1])))
    crop_width = min(source_w, source_h * target_w / target_h) / zoom
    crop_height = min(source_h, source_w * target_h / target_w) / zoom
    left = (source_w - crop_width) * (1 + offset_x) / 2
    top = (source_h - crop_height) * (1 + offset_y) / 2
    return left, top, left + crop_width, top + crop_height


def desktop_preview_layout(displays, work_size):
    """Fit the real virtual desktop (not PPI physical layout) in the preview.

    Each entry is (resolution, digital_offset). Return the scaled canvas and
    monitor boxes in panel coordinates, preserving per-monitor virtual positions
    and the gaps of an offset or mixed-resolution desktop. The wallpaper
    compositor uses these same digital offsets when combining monitor images.
    """
    if not displays or not has_positive_area(work_size):
        raise PreviewGeometryError
    if any(not has_positive_area(res) for res, _offset in displays):
        raise PreviewGeometryError
    left = min(offset[0] for _res, offset in displays)
    top = min(offset[1] for _res, offset in displays)
    right = max(offset[0] + res[0] for res, offset in displays)
    bottom = max(offset[1] + res[1] for res, offset in displays)
    canvas_size, canvas_pos, factor = fit_preview_canvas((right - left, bottom - top), work_size)
    rectangles = [
        (
            canvas_pos[0] + round((offset[0] - left) * factor),
            canvas_pos[1] + round((offset[1] - top) * factor),
            max(1, round(res[0] * factor)),
            max(1, round(res[1] * factor)),
        )
        for res, offset in displays
    ]
    return canvas_size, canvas_pos, rectangles


def comparison_hit_region(regions, point, fraction, *, tolerance=18):
    """Return pixels per complete split fraction if pointer touches the divider.

    A region is (screen_x, screen_y, screen_w, screen_h, crop_x,
    crop_w, source_canvas_w). Its crop may be scaled or repositioned
    independently from the source canvas in the desktop layout.
    """
    px, py = point
    for left, top, width, height, crop_left, crop_width, canvas_width in regions:
        if min(width, height, crop_width, canvas_width) <= 0:
            continue
        divider = left + (fraction * canvas_width - crop_left) * width / crop_width
        if not left <= divider <= left + width:
            continue
        if left <= px <= left + width and top <= py <= top + height and abs(px - divider) <= tolerance:
            return canvas_width * width / crop_width
    return None


def comparison_drag_fraction(original_fraction, delta_x, pixels_per_fraction):
    """Move a split handle without changing wallpaper pan or saved settings."""
    if pixels_per_fraction <= 0:
        return max(0.0, min(1.0, float(original_fraction)))
    return max(0.0, min(1.0, float(original_fraction) + delta_x / pixels_per_fraction))
