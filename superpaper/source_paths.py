"""Filesystem validation and identities for wallpaper source entries."""

import os

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp")


def is_valid_source(path):
    """Accept existing folders or supported image files, but not other files."""
    return os.path.isdir(path) or (os.path.isfile(path) and path.lower().endswith(IMAGE_EXTENSIONS))


def source_identity(path, target=""):
    """Return a stable key, keeping identical sources on different displays."""
    return str(target), os.path.normcase(os.path.realpath(os.path.expanduser(path)))


def resolved_wallpaper_selections(source_groups, previous_selected, replacements, *, targets=None):
    """Pin new Change Image selections into the exact render target slots.

    Each group is the source paths for one span area, display or span group.
    A previous selection is used only while still offered by its target, so a
    removed image cannot silently override the newly configured sources.
    Return None if any target lacks a valid local image.
    """

    def permitted(candidate, sources):
        if not candidate or not os.path.isfile(candidate) or not candidate.lower().endswith(IMAGE_EXTENSIONS):
            return False
        actual = os.path.normcase(os.path.realpath(candidate))
        for source in sources:
            base = os.path.normcase(os.path.realpath(source))
            if os.path.isfile(source) and actual == base:
                return True
            if os.path.isdir(source) and os.path.dirname(actual) == base:
                return True
        return False

    result = []
    previous = previous_selected or []
    target_ids = [str(index) for index in range(len(source_groups))] if targets is None else list(targets)
    if len(target_ids) != len(source_groups):
        raise ValueError("Source groups and target IDs must have matching lengths")
    for index, paths in enumerate(source_groups):
        choice = replacements.get(target_ids[index])
        if choice is None and len(source_groups) == 1:
            choice = replacements.get("")
        if not permitted(choice, paths):
            choice = previous[index] if index < len(previous) else None
        if not permitted(choice, paths):
            choice = None
            for source in paths:
                if permitted(source, paths):
                    choice = source
                    break
                if os.path.isdir(source):
                    for filename in sorted(os.listdir(source)):
                        image = os.path.join(source, filename)
                        if permitted(image, paths):
                            choice = image
                            break
                    if choice:
                        break
        if choice is None:
            return None
        result.append(choice)
    return result or None
