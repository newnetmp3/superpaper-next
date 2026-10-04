"""Resolution-independent image covering, zoom, pan and original-space crop."""

from PIL import Image

import superpaper.sp_logging as sp_logging
from superpaper.preview_geometry import original_frame_box


# resize image to fill given rectangle and do a positioned crop to size.
# Return output image.
def resize_to_fill(
    img,
    res,
    quality: str | Image.Resampling = Image.Resampling.LANCZOS,
    zoom=1.0,
    offset=(0.0, 0.0),
    reference_size=None,
):
    """Resize image to fill given rectangle and do a positioned crop to size.

    The image is always scaled so that it fully covers the target rectangle
    ``res`` (no letterboxing). ``zoom`` (>= 1.0) scales the image further in,
    cropping away more of the source. ``offset`` is an (x, y) pair in the range
    [-1.0, 1.0] that slides the crop window within the available overflow:
    0.0 keeps the default centered crop, -1.0 aligns to the left/top edge and
    +1.0 aligns to the right/bottom edge. The result always fills ``res``.

    If the image was enhanced, pass the oriented original source dimensions as
    reference_size. The cropped region is then mapped from original-image
    coordinates into the enhanced image, avoiding different framing after
    cloud processing.
    """
    if quality == "fast":
        quality = Image.Resampling.HAMMING
        reducing_gap = 1.5
    else:
        quality = Image.Resampling.LANCZOS
        reducing_gap = None

    if img.mode != "RGB":
        img = img.convert("RGB")

    # Sanitize positioning parameters.
    try:
        zoom = float(zoom)
    except TypeError, ValueError:
        zoom = 1.0
    if zoom < 1.0:
        zoom = 1.0
    try:
        offset_x = min(1.0, max(-1.0, float(offset[0])))
        offset_y = min(1.0, max(-1.0, float(offset[1])))
    except TypeError, ValueError, IndexError:
        offset_x, offset_y = 0.0, 0.0

    image_size = img.size  # returns image (width,height)
    if reference_size is not None and tuple(reference_size) != tuple(image_size):
        source_box = original_frame_box(reference_size, res, zoom, (offset_x, offset_y))
        x_factor = image_size[0] / reference_size[0]
        y_factor = image_size[1] / reference_size[1]
        enhanced_box = (
            source_box[0] * x_factor,
            source_box[1] * y_factor,
            source_box[2] * x_factor,
            source_box[3] * y_factor,
        )
        return img.resize(res, resample=quality, box=enhanced_box, reducing_gap=reducing_gap)
    if image_size == res and zoom == 1.0 and offset_x == 0.0 and offset_y == 0.0:
        # input image is already of the correct size, no action needed.
        return img

    # Scale so the image at least covers the target rectangle (cover fit),
    # then apply the additional user zoom. Using max() of the edge ratios
    # guarantees coverage regardless of aspect ratios.
    cover_multiplier = max(res[0] / image_size[0], res[1] / image_size[1])
    resize_multiplier = cover_multiplier * zoom
    # Guarantee the scaled image is never smaller than the target on either
    # edge despite rounding, so the final crop always yields exactly res.
    new_size = (
        max(round(resize_multiplier * image_size[0]), res[0]),
        max(round(resize_multiplier * image_size[1]), res[1]),
    )
    img = img.resize(new_size, resample=quality, reducing_gap=reducing_gap)

    extra_width = new_size[0] - res[0]
    extra_height = new_size[1] - res[1]
    # offset 0.0 -> centered crop (extra/2); -1.0 -> 0; +1.0 -> extra.
    left = round(extra_width / 2 * (1 + offset_x))
    top = round(extra_height / 2 * (1 + offset_y))
    # Clamp the crop origin so the window stays fully inside the image.
    left = min(max(left, 0), extra_width)
    top = min(max(top, 0), extra_height)
    crop_tuple = (left, top, left + res[0], top + res[1])
    cropped_res = img.crop(crop_tuple)
    if cropped_res.size == res:
        return cropped_res
    else:
        sp_logging.G_LOGGER.info("Error: result image not of correct size. crp:%s, res:%s", cropped_res.size, res)
        return cropped_res
