"""Lightweight local image adjustments used by preview and wallpaper renders.

Values are signed percentages, saved on each wallpaper profile. They are
computed locally after optional cloud enhancement and before local GLSL shaders;
neither clouds nor shader caches are invalidated just by moving a slider.
"""

from PIL import ImageDraw, ImageEnhance


def normalize_adjustment(value):
    """Clamp an adjustment to -100..100; corrupt old profiles are harmless."""
    try:
        return max(-100, min(100, int(value)))
    except TypeError, ValueError:
        return 0


def apply_local_adjustments(image, *, brightness=0, contrast=0, saturation=0):
    """Return the source unchanged at neutral settings to avoid extra passes."""
    brightness = normalize_adjustment(brightness)
    contrast = normalize_adjustment(contrast)
    saturation = normalize_adjustment(saturation)
    if not (brightness or contrast or saturation):
        return image
    result = image
    if brightness:
        result = ImageEnhance.Brightness(result).enhance(max(0.0, 1.0 + brightness / 100))
    if contrast:
        result = ImageEnhance.Contrast(result).enhance(max(0.0, 1.0 + contrast / 100))
    if saturation:
        result = ImageEnhance.Color(result).enhance(max(0.0, 1.0 + saturation / 100))
    return result


def split_local_preview(original, adjusted, fraction, *, has_adjustments=False):
    """Compare local processing without altering either input image.

    Left pixels are untouched original; right pixels are locally processed.
    The divider and captions remain visible even at neutral adjustment values,
    so the UI never looks broken just because both sides match. These overlays
    are strictly preview-only and are not saved to the wallpaper.
    """
    if original.size != adjusted.size:
        message = "Comparison images must have identical dimensions."
        raise ValueError(message)
    width, height = original.size
    if width <= 0 or height <= 0:
        message = "Comparison images must have positive dimensions."
        raise ValueError(message)

    position = round(width * max(0.0, min(1.0, float(fraction))))
    result = adjusted.convert("RGB").copy()
    if position:
        result.paste(original.convert("RGB").crop((0, 0, position, height)), (0, 0))
    draw = ImageDraw.Draw(result)

    # Keep the divider on the image even if sharpening and tone are neutral.
    line_x = min(width - 1, max(0, position))
    draw.line([(line_x, 0), (line_x, height - 1)], fill=(62, 172, 255), width=max(1, min(3, width // 500)))

    if width >= 300 and height >= 54:
        left_label = "ORIGINAL"
        right_label = "LOCAL EDITS" if has_adjustments else "NO LOCAL EDITS"
        pad = 6
        baseline = height - 25

        def label(text, x):
            box = draw.textbbox((0, 0), text)
            text_width = box[2] - box[0]
            draw.rounded_rectangle(
                (x, baseline, x + text_width + pad * 2, baseline + 19),
                radius=4,
                fill=(19, 38, 60),
            )
            draw.text((x + pad, baseline + 2), text, fill=(241, 249, 255))

        label(left_label, 8)
        right_width = draw.textbbox((0, 0), right_label)[2]
        label(right_label, max(8, width - right_width - pad * 2 - 8))
    return result
