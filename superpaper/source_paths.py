"""Filesystem validation and identities for wallpaper source entries."""

import os

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp")


def is_valid_source(path):
    """Accept existing folders or supported image files, but not other files."""
    return os.path.isdir(path) or (os.path.isfile(path) and path.lower().endswith(IMAGE_EXTENSIONS))


def source_identity(path, target=""):
    """Return a stable key, keeping identical sources on different displays."""
    return str(target), os.path.normcase(os.path.realpath(os.path.expanduser(path)))
