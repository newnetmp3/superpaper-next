"""The KDE picker launches a real system dialog; CI replaces only the process."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from superpaper.native_picker import IMAGE_FILTER, NativePickerError, kde_session, pick_kde_paths
from superpaper.source_paths import source_identity


def completed(code, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_kde_session_detection_including_wayland():
    assert kde_session({"XDG_CURRENT_DESKTOP": "KDE"})
    assert kde_session({"XDG_CURRENT_DESKTOP": "X-Cinnamon:KDE"})
    assert kde_session({"XDG_SESSION_DESKTOP": "plasma", "XDG_SESSION_TYPE": "wayland"})
    assert kde_session({"KDE_FULL_SESSION": "true"})
    assert not kde_session({"XDG_CURRENT_DESKTOP": "GNOME"})


def test_kdialog_opens_one_image_in_native_kde_dialog(tmp_path):
    observed = []

    def runner(args, **kwargs):
        observed.append((args, kwargs))
        return completed(0, str(tmp_path / "wallpaper with spaces.jpg") + "\n")

    result = pick_kde_paths(
        str(tmp_path),
        title="Change wallpaper image",
        environment={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"},
        which=lambda command: "/usr/bin/kdialog" if command == "kdialog" else None,
        runner=runner,
    )
    assert result == [str(tmp_path / "wallpaper with spaces.jpg")]
    args, kwargs = observed[0]
    assert args[:4] == ["/usr/bin/kdialog", "--title", "Change wallpaper image", "--getopenfilename"]
    assert args[4] == str(tmp_path)
    assert args[5] == IMAGE_FILTER
    assert "--multiple" not in args
    assert kwargs == {"capture_output": True, "text": True, "check": False}


def test_kdialog_supports_multiple_images_and_folder_choice(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return completed(0, "/tmp/one.png\n/tmp/two.webp\n")

    options = {"environment": {"XDG_CURRENT_DESKTOP": "KDE"}, "which": lambda _: "kdialog", "runner": runner}
    assert pick_kde_paths(str(tmp_path), multiple=True, **options) == ["/tmp/one.png", "/tmp/two.webp"]
    assert calls[-1][-2:] == ["--multiple", "--separate-output"]

    def runner_folder(*_args, **_kwargs):
        return completed(0, "/tmp/wallpaper folder\n")

    assert pick_kde_paths(str(tmp_path), folders=True, **(options | {"runner": runner_folder})) == [
        "/tmp/wallpaper folder"
    ]


def test_cancel_does_not_reopen_gtk_fallback(tmp_path):
    options = {"environment": {"XDG_CURRENT_DESKTOP": "KDE"}, "which": lambda _: "kdialog"}
    assert pick_kde_paths(str(tmp_path), runner=lambda *_a, **_kw: completed(1), **options) == []


def test_unavailable_kde_picker_explicitly_requests_toolkit_fallback(tmp_path):
    def runner(*_a, **_kw):
        pytest.fail("Should not start process without kdialog")

    assert (
        pick_kde_paths(
            str(tmp_path),
            environment={"XDG_CURRENT_DESKTOP": "GNOME"},
            which=lambda _: "kdialog",
            runner=runner,
        )
        is None
    )
    assert (
        pick_kde_paths(
            str(tmp_path),
            environment={"XDG_CURRENT_DESKTOP": "KDE"},
            which=lambda _: None,
            runner=runner,
        )
        is None
    )


def test_picker_errors_do_not_masquerade_as_cancellations(tmp_path):
    options = {"environment": {"XDG_CURRENT_DESKTOP": "KDE"}, "which": lambda _: "kdialog"}
    with pytest.raises(NativePickerError, match="exit code 2"):
        pick_kde_paths(str(tmp_path), runner=lambda *_a, **_kw: completed(2, stderr="display error"), **options)
    with pytest.raises(NativePickerError, match="could not be started"):
        pick_kde_paths(
            str(tmp_path),
            runner=lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disconnected")),
            **options,
        )


def _change_image_action():
    """Run only the real handler, without loading wxPython into headless CI."""
    gui = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(gui.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")
    method = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "onChangeImage")
    namespace = {"source_identity": source_identity}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<change-image>", "exec"), namespace)
    return namespace["onChangeImage"]


class FakeSourceList:
    def __init__(self, rows, selected):
        self.rows = rows
        self.selected = selected

    def GetColumnCount(self):
        return len(self.rows[0])

    def GetFirstSelected(self):
        return self.selected

    def GetItemCount(self):
        return len(self.rows)

    def GetItemText(self, index, column):
        return self.rows[index][column]

    def DeleteItem(self, index):
        self.rows.pop(index)
        self.selected = -1

    def Select(self, index):
        self.selected = index


def test_change_image_replaces_only_selected_monitor_without_discarding_others(tmp_path):
    new = str(tmp_path / "new.png")
    original = str(tmp_path / "original.jpg")
    other_monitor = str(tmp_path / "other.jpg")
    sources = FakeSourceList([["0", original], ["1", other_monitor]], selected=0)
    previews, edits = [], []
    panel = SimpleNamespace(
        path_listctrl=sources,
        _choose_native_sources=lambda **_kwargs: [new],
        _choose_source_target=lambda index: sources.GetItemText(index, 0),
        append_to_listctrl=lambda row: sources.rows.append(row),
        _preview_source_path=previews.append,
        _update_dirty_state=lambda: edits.append(True),
    )
    _change_image_action()(panel, None)
    assert ["1", other_monitor] in sources.rows
    assert ["0", new] in sources.rows
    assert ["0", original] not in sources.rows
    assert previews == [new]
    assert edits == [True]


def test_cancel_native_change_image_does_not_mutate_any_sources(tmp_path):
    original = str(tmp_path / "original.jpg")
    sources = FakeSourceList([[original]], selected=0)
    panel = SimpleNamespace(
        path_listctrl=sources,
        _choose_native_sources=lambda **_kwargs: [],
        _choose_source_target=lambda _index: pytest.fail("Cancelled selection must not choose target"),
        _preview_source_path=lambda _path: pytest.fail("Cancelled selection must not preview"),
    )
    _change_image_action()(panel, None)
    assert sources.rows == [[original]]


def test_old_generic_directory_browser_is_no_longer_referenced():
    root = Path(__file__).resolve().parents[1] / "superpaper"
    gui = (root / "gui.py").read_text(encoding="utf-8")
    dialogs = (root / "configuration_dialogs.py").read_text(encoding="utf-8")
    assert "BrowsePaths(" not in gui
    assert "class BrowsePaths(" not in dialogs
    assert "wx.GenericDirCtrl(" not in dialogs
    assert "self.studio_change_image.Bind(wx.EVT_BUTTON, self.onChangeImage)" in gui
    assert "pick_kde_paths(" in gui
