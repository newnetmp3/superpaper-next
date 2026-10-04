"""Wallpaper source enumeration, sorting and monitor-aware slideshow scheduling."""

import datetime
import os
import random

import superpaper.sp_logging as sp_logging
import superpaper.wallpaper_processing as wpproc
from superpaper.message_dialog import show_message_dialog


class SourceFileHandler:
    """
    Handles picking wallpapers from the assigned paths.

    Since multiple paths are supported per monitor, this class
    lists all valid images on a monitor by monitor basis and then
    orders the list according to sortmode. Allows for shuffling of the
    wallpapers, i.e. non-repeating randomized list, which is re-randomized
    once it has been exhausted.
    """

    def __init__(self, paths_array, sortmode, *, on_error=show_message_dialog):
        self._on_error = on_error
        # A list of lists if there is more than one monitor with distinct
        # input paths.
        self.all_files_in_paths = []
        self.paths_array = paths_array
        self.sortmode = sortmode
        self._pending_batch = None
        for paths_list in paths_array:
            list_of_images = []
            for path in paths_list:
                # Add list items to the end of the list instead of
                # appending the list to the list.
                if not os.path.exists(path):
                    message = f"A path was not found: '{path}'.\n\
Use absolute paths for best reliabilty."
                    sp_logging.G_LOGGER.info(message)
                    self._on_error(message, "Error")
                    continue
                else:
                    # List only images that are of supported type.
                    if os.path.isfile(path):
                        if path.lower().endswith(wpproc.G_SUPPORTED_IMAGE_EXTENSIONS):
                            list_of_images += [path]
                        else:
                            pass
                    else:
                        list_of_images += [
                            os.path.join(path, f)
                            for f in os.listdir(path)
                            if f.lower().endswith(wpproc.G_SUPPORTED_IMAGE_EXTENSIONS)
                        ]
            # The same file can be included through overlapping directories,
            # explicit paths, or symlinks. Keep its first occurrence only.
            unique_images = []
            seen_images = set()
            for image in list_of_images:
                identity = self._file_identity(image)
                if identity not in seen_images:
                    seen_images.add(identity)
                    unique_images.append(image)
            self.all_files_in_paths.append(unique_images)
        self.iterators = []
        for diplay_image_list in self.all_files_in_paths:
            self.iterators.append(self.ImageList(diplay_image_list, self.sortmode))

    def next_wallpaper_files(self, peek=False, _attempt=0):
        """Return a complete batch, avoiding cross-monitor duplicates when possible."""
        # Guard against unbounded recursion: a persistently invalid entry
        # (e.g. a dangling symlink that keeps being re-listed on reinit)
        # would otherwise loop forever. After this many reinit attempts,
        # give up without returning a partial positional batch (issue #135).
        max_attempts = 20
        # Reject an incomplete positional batch before consuming any of
        # the other iterators.
        if any(not iterable.files for iterable in self.iterators):
            return []
        if self._pending_batch is None:
            self._pending_batch = self._plan_batch()
        files, counters = self._pending_batch
        if not all(os.path.isfile(path) for path in files):
            if sp_logging.DEBUG:
                sp_logging.G_LOGGER.info("Ran into an invalid file, reinitializing..")
            if _attempt >= max_attempts:
                sp_logging.G_LOGGER.info(
                    "next_wallpaper_files: giving up after %d attempts due to persistently invalid files",
                    max_attempts,
                )
                self._pending_batch = None
                return []
            self.__init__(self.paths_array, self.sortmode)
            return self.next_wallpaper_files(peek=peek, _attempt=_attempt + 1)
        if peek:
            return list(files)
        for iterable, counter in zip(self.iterators, counters):
            iterable.counter = counter
        self._pending_batch = None
        return list(files)

    @staticmethod
    def _file_identity(path):
        return os.path.normcase(os.path.realpath(path))

    def _plan_batch(self):
        """Choose a maximum-distinct ordered assignment for all positions."""
        candidates = []
        for iterable in self.iterators:
            iterable.prepare_cycle()
            ordered_indices = list(range(iterable.counter, len(iterable.files))) + list(range(iterable.counter))
            candidates.append(
                [
                    (iterable.files[index], index + 1, self._file_identity(iterable.files[index]))
                    for index in ordered_indices
                ]
            )

        image_to_position = {}
        selected = [None] * len(candidates)

        def assign(position, visited):
            for path, counter, identity in candidates[position]:
                if identity in visited:
                    continue
                visited.add(identity)
                previous = image_to_position.get(identity)
                if previous is None or assign(previous, visited):
                    image_to_position[identity] = position
                    selected[position] = (path, counter)
                    return True
            return False

        # Reverse order preserves the earliest position's first choice when
        # several maximum matchings are otherwise equivalent.
        for position in reversed(range(len(candidates))):
            assign(position, set())

        # If uniqueness is impossible, duplicates are preferable to an
        # incomplete positional batch that could shift monitor assignments.
        for position, choices in enumerate(candidates):
            if selected[position] is None:
                path, counter, _identity = choices[0]
                selected[position] = (path, counter)

        completed = [choice for choice in selected if choice is not None]
        return ([choice[0] for choice in completed], [choice[1] for choice in completed])

    class ImageList:
        """Image list iterable that can reinitialize itself once it has been gone through."""

        def __init__(self, filelist, sortmode):
            self.counter = 0
            self.files = filelist
            self.sortmode = sortmode
            self.arrange_list()

        def __iter__(self):
            return self

        def _current_image(self):
            """Return the file at the current position, reshuffling when exhausted."""
            if not self.files:
                return None
            if self.counter >= len(self.files):
                self.counter = 0
                self.arrange_list()
            return self.files[self.counter]

        def prepare_cycle(self):
            """Arrange the next cycle once before coordinated batch planning."""
            if self.counter >= len(self.files):
                self.counter = 0
                self.arrange_list()

        def __next__(self):
            image = self._current_image()
            if image is not None:
                self.counter += 1
            return image

        def __peek__(self):
            return self._current_image()

        def arrange_list(self):
            """Reorders the image list as requested. Mostly for reoccuring shuffling."""
            if self.sortmode == "shuffle":
                random.shuffle(self.files)
            elif self.sortmode == "date_seeded_shuffle":
                today = datetime.datetime.now()  # noqa: DTZ005  # intentional local-time seed
                random.Random(today.strftime("%Y%m%d%H")).shuffle(self.files)
            elif self.sortmode == "alphabetical":
                self.files.sort()
            else:
                sp_logging.G_LOGGER.info("ImageList.arrange_list: unknown sortmode: %s", self.sortmode)
