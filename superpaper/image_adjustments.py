"""Lightweight local image adjustments used by preview and wallpaper renders.

Values are signed percentages, saved on each wallpaper profile. They are
computed locally after optional cloud enhancement and before local GLSL shaders;
neither clouds nor shader caches are invalidated just by moving a slider.
"""

from PIL import ImageEnhance


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
