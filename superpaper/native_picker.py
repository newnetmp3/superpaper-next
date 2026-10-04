"""Native file chooser integration for wallpaper images and source folders.

On Plasma, prefer KDialog's KDE file chooser over wxGTK's generic file tree.
Return None only when KDE's picker is unavailable (caller uses wx.FileDialog);
return [] on an actual user cancellation, without reopening another dialog.
"""

import os
import shutil
import subprocess

from superpaper.source_paths import IMAGE_EXTENSIONS

IMAGE_FILTER = "Images (" + " ".join(f"*{extension}" for extension in IMAGE_EXTENSIONS) + ")"


class NativePickerError(RuntimeError):
    """A launched native picker exited abnormally."""


def kde_session(environment=None):
    """Check KDE session markers without depending on a specific display server."""
    env = os.environ if environment is None else environment
    desktops = env.get("XDG_CURRENT_DESKTOP", "").replace(":", ";").split(";")
    return (
        any(desktop.strip().lower() in {"kde", "plasma"} for desktop in desktops)
        or env.get("XDG_SESSION_DESKTOP", "").lower() in {"kde", "plasma"}
        or env.get("KDE_FULL_SESSION", "").lower() in {"1", "true"}
    )


def pick_kde_paths(
    directory,
    *,
    folders=False,
    multiple=False,
    title="Choose wallpaper images",
    environment=None,
    which=shutil.which,
    runner=subprocess.run,
):
    """Return selected local paths; None requests fallback to a toolkit chooser.

    Only invoke KDialog inside a KDE/Plasma session, and only through a list
    argv (never through the shell). A genuine Cancel must *not* open the
    fallback chooser afterwards.
    """
    if not kde_session(environment):
        return None
    executable = which("kdialog")
    if not executable:
        return None

    directory = directory if os.path.isdir(directory) else os.path.expanduser("~")
    command = [executable, "--title", title]
    if folders:
        command.extend(["--getexistingdirectory", directory])
    else:
        command.extend(["--getopenfilename", directory, IMAGE_FILTER])
        if multiple:
            command.extend(["--multiple", "--separate-output"])
    try:
        result = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise NativePickerError(f"KDialog could not be started: {exc}") from exc

    if result.returncode == 1:
        return []
    if result.returncode != 0:
        error_message = f"KDialog failed with exit code {result.returncode}: {result.stderr.strip()}"
        raise NativePickerError(error_message)
    paths = [line for line in result.stdout.splitlines() if line]
    return paths if multiple else paths[:1]
